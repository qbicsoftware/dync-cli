import logging
import os
import tempfile
import hashlib

log = logging.getLogger(__name__)


class Storage:
    def __init__(self, path):
        log.info("Initialize storage at %s", path)
        self._path = path
        self._files = []
        self._destinations = []

    def add_file(self, meta, origin):
        destination = self._destination_from_meta(meta)
        log.info("Prepare new temporary file for destination %s", destination)
        self._destinations.append(destination)
        file = ChecksumFile(destination)
        self._files.append(file)
        return file

    def _destination_from_meta(self, meta):
        return "/tmp/dest"

    def __enter__(self):
        return self

    def __exit__(self, etype, evalue, trace):
        error = None
        for file in self._files:
            try:
                file.cleanup()
            except Exception as e:
                if error is not None:
                    error = e
        if error is not None:
            raise e


class ChecksumFile:
    def __init__(self, destination):
        self._tmpdir = tempfile.mkdtemp()
        self._tmppath = os.path.join(self._tmpdir, "upload")
        self._file = open(self._tmppath, 'wb')
        self._destination = destination
        self._hasher = hashlib.sha256()

    def write(self, data):
        self._hasher.update(data)
        self._file.write(data)

    def cleanup(self):
        self._file.close()
        try:
            os.unlink(self._tmppath)
        except Exception:
            pass
        try:
            os.rmdir(self._tmpdir)
        except Exception:
            pass

    def finalize(self, remote_checksum):
        try:
            self._file.close()
            ok = remote_checksum == self._hasher.digest()
            if ok:
                try:
                    os.rename(self._tmppath, self._destination)
                except Exception as e:
                    log.error("Failed to move %s to %s. Error: %s",
                              self._tmppath, self._destination, str(e))
                    raise
                log.info("Target file %s complete",
                         self._destination)
        finally:
            self.cleanup()
        return ok, self._hasher.digest()
