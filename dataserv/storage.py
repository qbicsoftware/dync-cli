"""Server side interface for writing incoming files to disk.

This module provides a default implementation that writes
all incoming files into a common directory and refuses new
files if a file with the same name exists or is currently
being uploaded.

All storage implementations must have an `add_file` method,
that takes the metadata provided by the client and the user id
of the client and returns a file-like object with `write`,
`abort` and `finalize(remote_checksum)` methods.
"""

import logging
import os
import tempfile
import hashlib
import uuid

log = logging.getLogger(__name__)


class Storage:
    def __init__(self, path):
        log.info("Initialize storage at %s", path)
        self._path = path
        self._files = {}
        self._destinations = set()

    def add_file(self, meta, user_id):
        file_id = uuid.uuid4().hex
        destination = self._destination_from_meta(meta)
        assert destination not in self._destinations
        log.info("Prepare new temporary file for destination %s", destination)
        file = ChecksumFile(file_id, destination, self)
        self._files[file_id] = file
        self._destinations.add(destination)
        return file

    def _remove_file(self, file):
        file_id = file._file_id
        assert file_id in self._files
        self._destinations.remove(file._destination)
        del self._files[file_id]

    def _destination_from_meta(self, meta):
        return "/tmp/dest"

    def __enter__(self):
        return self

    def __exit__(self, etype, evalue, trace):
        error = None
        for file in self._files.values():
            try:
                file.cleanup()
            except Exception as e:
                if error is None:
                    error = e
        if error is not None:
            raise error


class ChecksumFile:
    """File-like object that updates a checksum on each write."""
    def __init__(self, file_id, destination, storage):
        self._file_id = file_id
        self._storage = storage
        self._tmpdir = tempfile.mkdtemp()
        self._tmppath = os.path.join(self._tmpdir, "upload")
        self._file = open(self._tmppath, 'wb')
        self._destination = destination
        self._hasher = hashlib.sha256()
        self.nbytes_written = 0

    def write(self, data):
        self._hasher.update(data)
        self._file.write(data)
        self.chunks_written += 1

    def _cleanup(self):
        self._file.close()
        try:
            os.unlink(self._tmppath)
        except Exception:
            pass
        try:
            os.rmdir(self._tmpdir)
        except Exception:
            pass
        self._storage._remove_file(self)

    def abort(self):
        self._cleanup()

    def finalize(self, remote_checksum):
        if remote_checksum != self._hasher.digest():
            raise RuntimeError("Failed finalizing file: checksum mismatch")
        self._file.close()
        try:
            os.rename(self._tmppath, self._destination)
        except Exception as e:
            log.error("Failed to move %s to %s. Error: %s",
                      self._tmppath, self._destination, str(e))
            raise
        finally:
            self._cleanup()
        log.info("Target file %s complete", self._destination)
