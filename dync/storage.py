"""Server side interface for writing incoming files to disk.

This module provides a default implementation that writes
all incoming files into a common directory and refuses new
files if a file with the same name exists or is currently
being uploaded.

All storage implementations must have an `add_file` method,
that takes the metadata provided by the client and the user id
of the client and returns a file-like object with `write`,
`abort` and `finalize(remote_checksum)` methods and an
attribute `nbytes_written`.
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
        if not os.path.isdir(path):
            raise ValueError("Invalid storage destination: %s" % path)
        self._path = path
        self._files = {}
        self._destinations = set()

    def add_file(self, filename, meta, user_id):
        file_id = uuid.uuid4().hex
        destination = self._destination_from_meta(filename, meta)
        assert destination not in self._destinations
        assert not os.path.exists(destination)
        log.info("Prepare new temporary file for destination %s", destination)
        file = UploadFile(file_id, destination, self)
        self._files[file_id] = file
        self._destinations.add(destination)
        return file

    @property
    def num_active(self):
        return len(self._files)

    def _remove_file(self, file):
        file_id = file._file_id
        assert file_id in self._files
        self._destinations.remove(file._destination)
        del self._files[file_id]

    def _destination_from_meta(self, filename, meta):
        if filename != os.path.basename(filename) or filename.startswith('.'):
            raise ValueError("Inivalid filename: %s" % filename[:50])
        return os.path.join(self._path, filename)

    def __enter__(self):
        return self

    def __exit__(self, etype, evalue, trace):
        for file in list(self._files.values()):
            file._cleanup()


class UploadFile:
    """File-like object that ."""
    def __init__(self, file_id, destination, storage, tmp_prefix=None):
        self._file_id = file_id
        self._storage = storage
        self._tmpdir = tempfile.mkdtemp(dir=tmp_prefix)
        self._tmppath = os.path.join(self._tmpdir, "upload")
        self._file = open(self._tmppath, 'wb')
        self._destination = destination
        self._hasher = hashlib.sha256()
        self.nbytes_written = 0

    def write(self, data):
        self._file.write(data)
        self._hasher.update(data)
        self.nbytes_written += len(data)

    def _cleanup(self):
        self._file.close()
        try:
            os.unlink(self._tmppath)
        except Exception:
            pass
        os.rmdir(self._tmpdir)
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
        self._write_checksum()
        log.info("Target file %s complete", self._destination)

    def _write_checksum(self):
        checksum_destination = "{}.sha256".format(self._destination)
        try:
            with open(checksum_destination, 'w') as fh:
                fh.write("{}\t{}".format(self._hasher.hexdigest(),
                                         os.path.basename(self._destination)))
        except Exception as e:
            log.error("Failed to create checksum file {}. Error: {}"
                      .format(checksum_destination, str(e)))
            raise
        log.info("Wrote checksum file successfully.")
