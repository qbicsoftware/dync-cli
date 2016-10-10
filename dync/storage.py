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
import string
from .exceptions import InvalidUploadRequest

log = logging.getLogger(__name__)

BARCODE_REGEX = "Q[A-X0-9]{4}[0-9]{3}[A-X][A-X0-9]"
FINISHED_MARKER = ".MARKER_is_finished_"


class Storage:
    def __init__(self, opts):
        self._path = opts['path']
        log.info("Initialize storage at %s", self._path)
        if not os.path.isdir(self._path):
            raise ValueError("Invalid storage destination: %s" % self._path)
        self._files = {}
        self._destinations = set()
        self._opts = opts
        self.check_openbis()  # Check the openBis dropbox configuration

    def add_file(self, filename, meta, origin):
        file_id = uuid.uuid4().hex
        clean_name = clean_filename(filename)

        dest = self._destination_from_meta(filename, clean_name, meta, origin)
        if dest in self._destinations:
            raise InvalidUploadRequest("File is being uploaded already.")
        if os.path.exists(dest):
            raise InvalidUploadRequest("File exists on server.")
        log.info("Prepare new temporary file for destination %s", dest)
        file = UploadFile(
            file_id, dest, filename, clean_name, meta, origin,
            storage=self, tmp_prefix=self._opts['tmp_dir'])
        self._files[file_id] = file
        self._destinations.add(dest)
        return file

    @property
    def num_active(self):
        return len(self._files)

    def _remove_file(self, file):
        file_id = file._file_id
        assert file_id in self._files
        self._destinations.remove(file._destination)
        del self._files[file_id]

    def _destination_from_meta(self, filename, cleaned_name, meta, origin):
        """Assign file target location based on meta-data, origin and
        file suffix"""
        if filename != os.path.basename(filename) or filename.startswith('.'):
            raise InvalidUploadRequest("Invalid filename: %s" % filename[:50])

        if 'passthrough' in meta.keys():
            dest_dir = self._dest_from_passthrough(meta['passthrough'])
        else:
            dest_dir = self._find_openbis_dest(origin, filename, False)

        if dest_dir is None:
            raise InvalidUploadRequest(
                "File does not match any rule for incoming files.")

        return os.path.join(dest_dir, cleaned_name)

    def _dest_from_passthrough(self, passthrough):
        """Checks the passthrough directive for the manual storage.
        The directive will be a simple name, and a subdir with this name
        will be created in self._path. No slashes, spaces or dots
        are allowed."""
        if re.search(r'\W', passthrough):
            raise InvalidUploadRequest(
                'Only alphanumeric symbols and \'_\' are '
                'allowed as passthrough argument.'
            )
        return os.path.join(self._opts['manual'], passthrough)

    def _find_openbis_dest(self, origin, name, is_dir):
        """Determine the correct dropbox dependent on the settings in
        the configuration file."""
        for dropbox in self._opts['dropboxes']:
            regexp, path = dropbox['regexp'], dropbox['path']
            if 'origin' in dropbox and origin not in dropbox['origin']:
                continue
            if is_dir and not dropbox.get('match_dir', True):
                continue
            if not is_dir and not dropbox.get('match_file', True):
                continue
            if dropbox.get('requires_barcode', True):
                try:
                    extract_barcode(name)
                except ValueError:
                    continue
            if re.match(regexp, name):
                log.debug("file %s matches regex %s", name, regexp)
                return path

        return None

    def __enter__(self):
        return self

    def __exit__(self, etype, evalue, trace):
        for file in list(self._files.values()):
            file._cleanup()

    def check_openbis(self):
        """Check if the settings for the openBis dropboxes are correct."""
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
                        raise ValueError(
                            "Invalid regular expression: %s" % conf[key])
                elif key == 'path':
                    if not os.path.isdir(conf[key]):
                        raise ValueError(
                            "Not a directory: %s" % conf[key])
                    if not os.path.isabs(conf[key]):
                        raise ValueError(
                            "Not an absolute path: %s" % conf[key])
                elif key == 'origin':
                    if not isinstance(conf[key], list):
                        raise ValueError(
                            "'origin' in 'openbis' section must be a list")
                elif key in ['match_dir', 'match_file']:
                    pass
                elif key == 'requires_barcode':
                    pass
                else:
                    raise ValueError(
                        "Unexpected option %s in section 'openbis'" % key)


class UploadFile:
    """File-like object that ."""
    def __init__(self, file_id, destination, filename, clean_name, meta,
                 origin, storage, tmp_prefix=None):
        self._file_id = file_id
        self._meta = meta
        self._filename = filename
        self._clean_name = clean_name
        self._origin = origin
        self._storage = storage
        self._tmpdir = tempfile.mkdtemp(dir=tmp_prefix)
        self._tmppath = os.path.join(
            self._tmpdir, os.path.basename(destination)
        )
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
        try:
            os.rmdir(self._tmpdir)
        except Exception:
            pass
        self._storage._remove_file(self)

    def abort(self):
        self._cleanup()

    def finalize(self, remote_checksum):
        """Empty buffers, move files, write checksum in file
        and write marker file when finished"""
        if remote_checksum != self._hasher.digest():
            raise RuntimeError("Failed finalizing file: checksum mismatch")

        flush(self._file)
        self._file.close()

        self._write_checksum()

        # We need to flush the direcory to make sure the file metadata
        # has been written to disk.
        tmpdirfd = os.open(self._tmpdir, os.O_RDONLY)
        try:
            os.fsync(tmpdirfd)
        finally:
            os.close(tmpdirfd)

        # Move directory to target destination
        try:
            os.rename(self._tmpdir, self._destination)
        except Exception as e:
            log.error("Failed to move %s to %s. Error: %s",
                      self._tmpdir, self._destination, str(e))
            raise
        finally:
            self._cleanup()

        destbasefd = os.open(os.path.dirname(self._destination), os.O_RDONLY)
        try:
            os.fsync(destbasefd)
        finally:
            os.close(destbasefd)

        # Write marker file when finished
        self._write_marker()
        log.info("Target file %s complete", self._destination)

    def _write_checksum(self):
        checksum_destination = "{}.sha256".format(self._tmppath)
        with open(checksum_destination, 'w') as fh:
            fh.write("{}\t{}".format(self._hasher.hexdigest(),
                                     os.path.basename(self._destination)))
            flush(fh)
        log.info("Wrote checksum file successfully.")

    def _write_marker(self):
        try:
            extract_barcode(self._destination)
        except ValueError:
            return
        parent_dir = os.path.dirname(self._destination)
        marker_file = os.path.join(parent_dir, FINISHED_MARKER +
                                   os.path.basename(self._destination))
        try:
            with open(marker_file, 'x') as fh:
                log.info("Create marker file: {}".format(marker_file))
                flush(fh)
        except FileExistsError:
            log.error("Marker file already exists")


def flush(fh):
    """Write all internal buffer to disk"""
    fh.flush()
    os.fsync(fh.fileno())


def clean_filename(path):
    """Generate a sane (alphanumeric) filename for path."""
    allowed_chars = string.ascii_letters + string.digits + '_.'
    stem, suffix = os.path.splitext(os.path.basename(path))
    cleaned_stem = ''.join(i for i in stem if i in allowed_chars)
    cleaned_stem = cleaned_stem.lstrip('.')
    if not cleaned_stem:
        raise ValueError("Invalid file name: %s", stem + suffix)

    if not all(i in allowed_chars for i in suffix):
        raise ValueError("Bad file suffix: " + suffix)

    return cleaned_stem + suffix


def extract_barcode(path):
    """Extract an OpenBis barcode from the file name.
    If a barcode is found, return it. Raise ValueError if no barcode,
    or more that one barcode has been found.
    Barcodes must match this regular expression: [A-Z]{5}[0-9]{3}[A-Z][A-Z0-9]
    """
    stem, suffix = os.path.splitext(os.path.basename(path))
    barcodes = re.findall(BARCODE_REGEX, stem)
    valid_barcodes = [b for b in barcodes if is_valid_barcode(b)]
    if len(barcodes) != len(valid_barcodes):
        log.warn("Invalid barcode in file name: %s",
                    set(barcodes) - set(valid_barcodes))
    if not barcodes:
        raise ValueError("no barcodes found")
    if len(set(barcodes)) > 1:
        raise ValueError("more than one barcode in filename")
    return barcodes[0]


def generate_openbis_name(path):
    r"""Generate a sane file name from the input file.
    Copy the barcode to the front and remove invalid characters.
    Raise ValueError if the filename does not contain a barcode.
    Example
    -------
    >>> path = "stüpid\tname(<QJFDC010EU.).>ä.raW"
    >>> generate_openbis_name(path)
    'QJFDC010EU_stpidname.raw'
    """
    cleaned_name = clean_filename(path)
    barcode = extract_barcode(cleaned_name)
    name = cleaned_name.replace(barcode, "")
    return barcode + '_' + name


def is_valid_barcode(barcode):
    """Check if barcode is a valid OpenBis barcode."""
    if re.match('^' + BARCODE_REGEX + '$', barcode) is None:
        return False
    csum = sum(ord(c) * (i + 1) for i, c in enumerate(barcode[:-1]))
    csum = csum % 34 + 48
    if csum > 57:
        csum += 7
    if barcode[-1] == chr(csum):
        return True
    return False
