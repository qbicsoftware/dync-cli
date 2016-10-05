import tempfile
import os
import hashlib
import shutil
from nose.tools import assert_raises
from dync import storage


def test_cleanup():
    config = {'path': 'path', 'dropboxes': []}
    store = storage.Storage(config)
    with store:
        store.add_file('bar', {}, 'itsme')
        assert store.num_active == 1
    assert store.num_active == 0


class TestStorage:
    def setUp(self):
        self.path = tempfile.mkdtemp()
        storage_config = {'path': self.path, 'dropboxes': []}
        self.storage = storage.Storage(storage_config)

    def tearDown(self):
        self.storage.__exit__(None, None, None)
        shutil.rmtree(self.path)

    def test_add_file(self):
        self.storage.add_file("filename", {}, "user-id")

    def test_fn_unique(self):
        self.storage.add_file("filename", {}, "user-id")
        with assert_raises(Exception):
            self.storage.add_file("filename", {}, "user-id")

    def test_finalize(self):
        file = self.storage.add_file("a", {}, "b")
        remote_hash = hashlib.sha256()
        file.finalize(remote_hash.digest())
        with assert_raises(Exception):
            self.storage.add_file("a", {}, "b")
        os.unlink(os.path.join(self.path, "a"))
        self.storage.add_file("a", {}, "b")

    def test_abort(self):
        file = self.storage.add_file("a", {}, "b")
        file.abort()
        file = self.storage.add_file("a", {}, "b")

    def test_write(self):
        file = self.storage.add_file("a", {}, "b")
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
        with assert_raises(ValueError):
            self.storage.add_file("..", {}, "b")
        with assert_raises(ValueError):
            self.storage.add_file(".", {}, "b")
        with assert_raises(ValueError):
            self.storage.add_file(".blubb", {}, "b")
        with assert_raises(ValueError):
            self.storage.add_file("/hallo/", {}, "b")
        with assert_raises(ValueError):
            self.storage.add_file("/", {}, "b")
        with assert_raises(ValueError):
            self.storage.add_file("hallo/hi", {}, "b")
