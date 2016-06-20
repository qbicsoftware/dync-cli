import tempfile
import shutil
from nose.tools import assert_raises
import zmq
import uuid
import os
from unittest import mock
from dync import server, storage, messages
import hashlib


class ClientSim:
    def __init__(self):
        pass


def socket_pair(ctx, type1, type2, id1=None, id2=None):
    addr = "inproc://" + uuid.uuid4().hex
    sock1 = ctx.socket(type1)
    sock1.set(zmq.LINGER, 0)
    if id1 is not None:
        sock1.set(zmq.IDENTITY, id1)
    sock1.bind(addr)
    sock2 = ctx.socket(type2)
    if id2 is not None:
        sock2.set(zmq.IDENTITY, id2)
    sock2.set(zmq.LINGER, 0)
    sock2.connect(addr)
    return sock1, sock2


def check_empty(sock):
    with assert_raises(zmq.Again):
        sock.recv_multipart(zmq.NOBLOCK)


class TestUpload:
    def setUp(self):
        self.ctx = zmq.Context()
        self.ssock, self.csock = socket_pair(
            self.ctx, zmq.ROUTER, zmq.DEALER, id2=b"test")

        self.storage_dir = tempfile.mkdtemp()
        self.storage = storage.Storage(self.storage_dir)
        self.conn = messages.ServerConnection(self.ssock, b"test")
        self.file = self.storage.add_file("name", {}, "user-id")
        self.upload = server.Upload(self.conn, self.file, "user-id", 10)
        reply = self.csock.recv_multipart()

        self.client = messages.ClientConnection(self.csock)
        assert reply[0] == b"upload-approved"

    def tearDown(self):
        for sock in [self.csock, self.ssock]:
            check_empty(sock)
            sock.close()
        self.ctx.term()
        self.storage.__exit__(None, None, None)
        try:
            shutil.rmtree(self.storage_dir)
        except Exception:
            pass

    def test_init(self):
        file = self.storage.add_file("name2", {}, "user-id")
        upload = server.Upload(self.conn, file, "user-id", 10)
        reply = self.csock.recv_multipart()
        assert reply[0] == b"upload-approved"
        assert upload.seconds_since_active() < 1

    def test_post(self):
        assert self.file.nbytes_written == 0
        self.client.send_post_chunk(0, b"hallo", False)
        msg = messages.recv_msg_server(self.ssock)
        fin, _ = self.upload.handle_msg(msg)
        assert not fin
        assert self.file.nbytes_written == 5

        self.client.send_post_chunk(5, b"hallo", False)
        msg = messages.recv_msg_server(self.ssock)
        fin, _ = self.upload.handle_msg(msg)
        assert not fin
        assert self.file.nbytes_written == 10

    def test_invalid_post(self):
        assert self.file.nbytes_written == 0
        self.client.send_post_chunk(0, b"hallo", False)
        msg = messages.recv_msg_server(self.ssock)
        fin, _ = self.upload.handle_msg(msg)
        assert not fin
        assert self.file.nbytes_written == 5

        self.client.send_post_chunk(4, b"hallo", False)
        msg = messages.recv_msg_server(self.ssock)
        fin, _ = self.upload.handle_msg(msg)
        assert not fin
        assert self.file.nbytes_written == 5

        self.client.send_post_chunk(6, b"hallo", True, b"\0" * 32)
        msg = messages.recv_msg_server(self.ssock)
        fin, _ = self.upload.handle_msg(msg)
        assert not fin
        assert self.file.nbytes_written == 5

    def test_invalid_message(self):
        self.upload.handle_msg(messages.PostFileMsg(1, 2, 3, 4, 5))

    def test_disk_full(self):
        self.client.send_post_chunk(0, b"h", False)
        msg = messages.recv_msg_server(self.ssock)
        with mock.patch('dync.storage.UploadFile.write', side_effect=OSError):
            fin, _ = self.upload.handle_msg(msg)
            assert fin
        reply = self.csock.recv_multipart(zmq.NOBLOCK)
        print(reply)
        assert reply[0] == b"error"
        assert not len(self.storage._destinations)

    def test_send_invalid_chsum(self):
        self.client.send_post_chunk(0, b"", True, b"\0" * 32)
        msg = messages.recv_msg_server(self.ssock)
        fin, _ = self.upload.handle_msg(msg)
        assert fin
        reply = self.csock.recv_multipart(zmq.NOBLOCK)
        assert reply[0] == b"error"

    def test_send_last_chunk(self):
        cksum = hashlib.sha256().digest()
        dest = next(iter(self.storage._destinations))
        self.client.send_post_chunk(0, b"", True, cksum)
        msg = messages.recv_msg_server(self.ssock)
        fin, _ = self.upload.handle_msg(msg)
        assert fin
        reply = self.csock.recv_multipart(zmq.NOBLOCK)
        assert reply[0] == b"upload-finished"
        assert not len(self.storage._destinations)
        assert os.path.exists(dest)

    def test_invalid_dest(self):
        shutil.rmtree(self.storage_dir)
        cksum = hashlib.sha256().digest()
        self.client.send_post_chunk(0, b"", True, cksum)
        msg = messages.recv_msg_server(self.ssock)
        fin, _ = self.upload.handle_msg(msg)
        assert fin
        reply = self.csock.recv_multipart(zmq.NOBLOCK)
        assert reply[0] == b"error"
        assert self.storage.num_active == 0

    def test_client_error(self):
        self.client.send_error(500, "hi")
        msg = messages.recv_msg_server(self.ssock)
        fin, _ = self.upload.handle_msg(msg)
        assert fin
        assert self.storage.num_active == 0

    def test_request_status(self):
        self.client.send_query_status()
        msg = messages.recv_msg_server(self.ssock)
        fin, credits = self.upload.handle_msg(msg)
        assert not fin
        assert credits == 0
        reply = messages.recv_msg_client(self.csock)
        assert reply.command == b"status-report"
        assert reply.seek == 0
