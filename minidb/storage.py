"""Storage: how MiniDB keeps data on disk, safely.

Two files, one idea
-------------------
* ``<name>.minidb``     -- the snapshot.  A compact copy of all
  live data, written by CHECKPOINT.
* ``<name>.minidb-wal`` -- the write-ahead log (WAL).  Every change
  is appended here FIRST, with fsync, before MiniDB says "done".
  If the process is killed, the WAL is replayed on the next open.

File format (both files use the same record format)
---------------------------------------------------
Snapshot file:
    first 7 bytes:  b"MINIDB1"          (the magic header)
    then:           records, one after another

WAL file:
    records only, no header.  An empty WAL means "no changes
    since the last checkpoint".

One record:
    4 bytes  -- length of the payload, big-endian unsigned int
    4 bytes  -- CRC32 checksum of the payload
    N bytes  -- the payload: one JSON object, UTF-8 encoded

    Example payload: {"op": "insert", "table": "users",
                      "row": {"id": 1, "name": "Abir"}}

Why this format?
* Length prefix: we always know where the next record starts,
  even after a crash in the middle of a write.
* Checksum: if the last record was only half-written when the
  power died, its checksum will not match and we stop there
  instead of loading broken data.  That half-written tail is
  called a "torn write" and ignoring it is the correct behaviour.

Crash safety, in one sentence: a row is durable only after its
WAL record has been flushed to disk with os.fsync().
"""

from __future__ import annotations

import json
import os
import struct
import zlib
from pathlib import Path

MAGIC = b"MINIDB1"
# ">I" = big-endian unsigned int (4 bytes).  ">II" = length + crc.
_HEADER = struct.Struct(">I")
_RECORD_HEAD = struct.Struct(">II")


def _encode_record(payload: dict) -> bytes:
    """Turn one dict into length + checksum + JSON bytes."""
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    crc = zlib.crc32(raw) & 0xFFFFFFFF
    return _RECORD_HEAD.pack(len(raw), crc) + raw


def _read_records_from(data: bytes, start: int) -> tuple[list[dict], int]:
    """Read length-prefixed records from bytes starting at offset.

    Returns (records, good_bytes): the records that were complete
    and had a matching checksum, and how many bytes of the input
    were good.  Reading stops at the first bad or incomplete
    record -- that is a torn tail from a crash, not an error.
    """
    records: list[dict] = []
    pos = start
    total = len(data)
    while pos < total:
        # Need at least 8 bytes for length + checksum.
        if pos + _RECORD_HEAD.size > total:
            break
        length, crc = _RECORD_HEAD.unpack_from(data, pos)
        # A record larger than 64 MB is not a real record; the file
        # is corrupt past this point, so stop here.
        if length > 64 * 1024 * 1024:
            break
        end = pos + _RECORD_HEAD.size + length
        if end > total:
            break  # incomplete record: torn write, stop here
        raw = data[pos + _RECORD_HEAD.size : end]
        if (zlib.crc32(raw) & 0xFFFFFFFF) != crc:
            break  # checksum mismatch: torn write, stop here
        try:
            records.append(json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            break
        pos = end
    return records, pos


class Storage:
    """Owns the two files on disk.  Knows nothing about SQL."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.wal_path = Path(str(path) + "-wal")
        # Make sure the directory exists before we write anything.
        if self.path.parent != Path(""):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._wal_file = None  # opened lazily by append()

    # -- loading ----------------------------------------------------

    def load(self) -> list[dict]:
        """Load snapshot + WAL and return all records, in order.

        Snapshot records come first, then WAL records.  A torn
        WAL tail is ignored and then truncated, so the next open
        starts from a clean file.
        """
        records: list[dict] = []
        if self.path.exists():
            data = self.path.read_bytes()
            if data:
                if not data.startswith(MAGIC):
                    raise ValueError(
                        f"{self.path} is not a MiniDB file "
                        f"(bad magic header)."
                    )
                snap_records, _ = _read_records_from(data, len(MAGIC))
                records.extend(snap_records)
        if self.wal_path.exists():
            data = self.wal_path.read_bytes()
            if data:
                wal_records, good = _read_records_from(data, 0)
                records.extend(wal_records)
                # If part of the WAL was torn, cut it off now so a
                # later append does not write after garbage.
                if good < len(data):
                    with open(self.wal_path, "r+b") as f:
                        f.truncate(good)
                        f.flush()
                        os.fsync(f.fileno())
        return records

    # -- appending --------------------------------------------------

    def append(self, record: dict) -> None:
        """Append one record to the WAL and fsync it.

        This is the durability point: after this returns, the
        record will survive a process kill (os._exit / kill -9).
        It will NOT survive a full machine power loss unless the
        disk itself honours fsync, which is true for normal disks.
        """
        encoded = _encode_record(record)
        # Open in append mode each time: simple, and safe if the
        # file was truncated by load() or checkpoint().
        with open(self.wal_path, "ab") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())

    def wal_size(self) -> int:
        """Current WAL size in bytes (0 if there is no WAL)."""
        if self.wal_path.exists():
            return self.wal_path.stat().st_size
        return 0

    # -- checkpoint -------------------------------------------------

    def checkpoint(self, records: list[dict]) -> None:
        """Write all live records as a new snapshot, clear the WAL.

        Steps, in order:
        1. Write the new snapshot to a temporary file and fsync it.
        2. Atomically rename it over the old snapshot.  A rename
           inside one directory is atomic on Linux/Windows/macOS.
        3. fsync the directory, so the rename itself is durable.
        4. Delete the WAL (it is now fully inside the snapshot).

        If we crash between steps, the old snapshot is still valid
        and the WAL is still there, so no data is lost.
        """
        tmp_path = Path(str(self.path) + ".tmp")
        with open(tmp_path, "wb") as f:
            f.write(MAGIC)
            for record in records:
                f.write(_encode_record(record))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.path)
        # fsync the directory so the rename survives a crash.
        try:
            dir_fd = os.open(str(self.path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # Some filesystems do not allow fsync on a directory;
            # the data itself is already fsynced above.
            pass
        if self.wal_path.exists():
            self.wal_path.unlink()

    def close(self) -> None:
        """Nothing to close: append() opens and closes per write."""
        return None
