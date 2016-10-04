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
import re
from .exceptions import InvalidUploadRequest

log = logging.getLogger(__name__)


class Storage:
    def __init__(self, path, opts):
        log.info("Initialize storage at %s", path)
        if not os.path.isdir(path):
            raise ValueError("Invalid storage destination: %s" % path)
        self._path = path
        self._files = {}
        self._destinations = set()
        self._opts = opts
        # Check the openBis dropbox configuration
        self.check_openbis()

    def add_file(self, filename, meta):
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
        self._assign_destination(meta)
        destination = meta['destination']
        if filename != os.path.basename(filename) or filename.startswith('.'):
            raise ValueError("Invalid filename: %s" % filename[:50])

        try:
            os.makedirs(destination)
        except Exception:
            error_msg = 'Could not create destination folder {}'.format()
            log.error(error_msg)
            raise Exception(error_msg)

        if os.path.isdir(destination) and os.access(destination, os.W_OK):
            return os.path.join(destination, filename)
        return os.path.join(self._path, filename)

    def _assign_destination(self, meta):
        """Helper function for self._destination_from_meta()
        Searches for key:value directives for manual storage or uses the
        respective openBis dropboxes, if no meta information is given.
        The respective settings are made in the config."""
        if 'passthrough' in meta.keys():
            meta['destination'] = os.path.join(
                self._opts['manual'], meta['passthrough']
            )
        # TODO check openBis config in which dropbox the data has to be
        # TODO assigned. Also check for barcode etc.
        else:
            raise Exception("Could not determine correct storage "
                            "destination for file")

    def __enter__(self):
        return self

    def __exit__(self, etype, evalue, trace):
        for file in list(self._files.values()):
            file._cleanup()

    def check_openbis(self):
        """Checks if the settings for the openBis
        dropboxes are correct."""
        config = self._opts['dropboxes']
        if not isinstance(config, list):
            raise InvalidUploadRequest(
                "Config section 'openbis' is not a list")
        for conf in config:
            for key in conf:
                if key == 'regexp':
                    try:
                        re.compile(conf[key])
                    except re.error:
                        raise InvalidUploadRequest(
                            "Invalid regular expression: %s" % conf[key])
                elif key == 'path':
                    if not os.path.isdir(conf[key]):
                        raise InvalidUploadRequest(
                            "Not a directory: %s" % conf[key])
                    if not os.path.isabs(conf[key]):
                        raise InvalidUploadRequest(
                            "Not an absolute path: %s" % conf[key])
                elif key == 'origin':
                    if not isinstance(conf[key], list):
                        raise InvalidUploadRequest(
                            "'origin' in 'openbis' section must be a list")
                elif key in ['match_dir', 'match_file']:
                    pass
                else:
                    raise InvalidUploadRequest(
                        "Unexpected option %s in section 'openbis'" % key)


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
        with open(checksum_destination, 'w') as fh:
                fh.write("{}\t{}".format(self._hasher.hexdigest(),
                                         os.path.basename(self._destination)))
        log.info("Wrote checksum file successfully.")
