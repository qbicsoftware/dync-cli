from nose import with_setup
from nose.tools import assert_raises
import zmq

from dync import messages


class TestServerConnection:
    def setUp(self):
        self.ctx = zmq.Context.instance()
        self.conn_id = b'aaa'
        self.pull = self.ctx.socket(zmq.DEALER)
        self.push = self.ctx.socket(zmq.ROUTER)
        self.push.set(zmq.LINGER, 0)
        self.pull.set(zmq.LINGER, 0)
        self.pull.set(zmq.IDENTITY, self.conn_id)
        self.pull.bind('inproc://test-messages')
        self.push.connect('inproc://test-messages')
        self.conn = messages.ServerConnection(self.push, self.conn_id)

    def tearDown(self):
        with assert_raises(zmq.error.Again):
            self.pull.recv_multipart(zmq.NOBLOCK)
        self.pull.close()
        self.push.close()

    def test_error(self):
        self.conn.send_error(500, 'error')
        msg = messages.recv_msg_client(self.pull)
        assert msg.command == b'error'
        assert msg.code == 500
        assert msg.msg == 'error'

    def test_pong(self):
        self.conn.send_pong()
        msg = messages.recv_msg_client(self.pull)
        assert msg.command == b"pong"

    def test_transfer_credit(self):
        self.conn.send_tranfer_credit(amount=15, ack_until_byte=2 ** 50)
        msg = messages.recv_msg_client(self.pull)
        assert msg.command == b"transfer-credit"
        assert msg.amount == 15
        assert msg.ack_until_byte == 2 ** 50
        with assert_raises(OverflowError):
            self.conn.send_tranfer_credit(2 ** 50, 1)

    def test_upload_approved(self):
        self.conn.send_upload_approved(credit=10, max_credit=20, chunksize=50)
        msg = messages.recv_msg_client(self.pull)
        assert msg.command == b"upload-approved"
        assert msg.credit == 10
        assert msg.max_credit == 20
        assert msg.chunksize == 50

    def test_upload_finished(self):
        self.conn.send_upload_finished("an_id")
        msg = messages.recv_msg_client(self.pull)
        assert msg.command == b"upload-finished"
        assert msg.upload_id == "an_id"


class TestClientConnection:
    def setUp(self):
        self.ctx = zmq.Context.instance()
        self.conn_id = b'aaa'
        self.pull = self.ctx.socket(zmq.DEALER)
        self.push = self.ctx.socket(zmq.ROUTER)
        self.push.set(zmq.LINGER, 0)
        self.pull.set(zmq.LINGER, 0)
        self.pull.set(zmq.IDENTITY, self.conn_id)
        self.pull.bind('inproc://test-messages')
        self.push.connect('inproc://test-messages')
        self.conn = messages.ClientConnection(self.pull)

    def tearDown(self):
        with assert_raises(zmq.error.Again):
            self.push.recv_multipart(zmq.NOBLOCK)
        self.pull.close()
        self.push.close()

    def test_error(self):
        self.conn.send_error(500, 'error')
        msg = messages.recv_msg_server(self.push)
        assert msg.command == b'error'
        assert msg.code == 500
        assert msg.msg == 'error'

    def test_ping(self):
        self.conn.send_ping()
        msg = messages.recv_msg_server(self.push)
        assert msg.command == b"ping"

    def test_post_chunk(self):
        self.conn.send_post_chunk(seek=0, data=b"data", is_last=False)
        msg = messages.recv_msg_server(self.push)
        assert msg.command == b"post-chunk"
        assert msg.data == b"data"
        assert not msg.is_last

        self.conn.send_post_chunk(0, b"data", is_last=True, checksum=b"aa")
        msg = messages.recv_msg_server(self.push)
        assert msg.command == b"post-chunk"
        assert msg.data == b"data"
        assert msg.is_last
        assert msg.checksum == b"aa"

    def test_post_file(self):
        self.conn.send_post_file(name="hallo", meta={"a": 5})
        msg = messages.recv_msg_server(self.push)
        assert msg.command == b"post-file"
        assert msg.name == "hallo"
        assert msg.meta == {"a": 5}
