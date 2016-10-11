import tempfile
import os
import hashlib
import shutil
from nose.tools import assert_raises
from dync import storage
from dync.exceptions import InvalidUploadRequest


def test_cleanup():
    path = tempfile.mkdtemp()
    test_target_dir = os.path.join(path, 'test')
    os.mkdir(test_target_dir)
    config = {
        'path': path,
        'tmp_dir': path,
        'manual': path,
        'storage': path,
        'dropboxes': []}
    store = storage.Storage(config)
    with store:
        store.add_file('bar', {'passthrough': 'test'}, 'itsme')
        assert store.num_active == 1
    assert store.num_active == 0


class TestStorage:
    def setUp(self):
        self.path = tempfile.mkdtemp()
        self.test_target_dir = os.path.join(self.path, 'test')
        os.mkdir(self.test_target_dir)
        storage_config = {
            'path': self.path,
            'tmp_dir': self.path,
            'manual': self.path,
            'storage': self.path,
            'dropboxes': []}
        self.storage = storage.Storage(storage_config)

    def tearDown(self):
        self.storage.__exit__(None, None, None)
        shutil.rmtree(self.path)

    def test_add_file(self):
        self.storage.add_file("filename", {'passthrough': 'test'}, "user-id")

    def test_fn_unique(self):
        self.storage.add_file("filename", {'passthrough': 'test'}, "user-id")
        with assert_raises(Exception):
            self.storage.add_file(
                "filename", {'passthrough': 'test'}, "user-id")

    def test_finalize(self):
        file = self.storage.add_file("a", {'passthrough': 'test'}, "b")
        remote_hash = hashlib.sha256()
        file.finalize(remote_hash.digest())
        with assert_raises(Exception):
            self.storage.add_file("a", {}, "b")

        shutil.rmtree(os.path.join(self.test_target_dir, "a"))
        self.storage.add_file("a", {'passthrough': 'test'}, "b")

    def test_abort(self):
        file = self.storage.add_file("a", {'passthrough': 'test'}, "b")
        file.abort()
        file = self.storage.add_file("a", {'passthrough': 'test'}, "b")

    def test_write(self):
        file = self.storage.add_file("a", {'passthrough': 'test'}, "b")
        assert file.nbytes_written == 0
        file.write(b"")
        assert file.nbytes_written == 0
        file.write(b"a" * 100)
        assert file.nbytes_written == 100
        remote_hash = hashlib.sha256()
        remote_hash.update(b"a" * 100)
        file.finalize(remote_hash.digest())
        assert not os.path.exists(file._tmpdir)

    def test_invalid_filename(self):
        with assert_raises(InvalidUploadRequest):
            self.storage.add_file("..", {'passthrough': 'test'}, "b")
        with assert_raises(InvalidUploadRequest):
            self.storage.add_file(".", {'passthrough': 'test'}, "b")
        with assert_raises(InvalidUploadRequest):
            self.storage.add_file(".blubb", {'passthrough': 'test'}, "b")
        with assert_raises(InvalidUploadRequest):
            self.storage.add_file("/hallo/", {'passthrough': 'test'}, "b")
        with assert_raises(InvalidUploadRequest):
            self.storage.add_file("/", {'passthrough': 'test'}, "b")
        with assert_raises(InvalidUploadRequest):
            self.storage.add_file("hallo/hi", {'passthrough': 'test'}, "b")
