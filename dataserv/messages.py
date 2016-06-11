import collections
import json


ErrorMsg = collections.namedtuple(
    'ErrorMsg',
    ['command', 'connection', 'origin', 'code', 'msg'])

# Messages send by client

PostFileMsg = collections.namedtuple(
    'PostFileMsg',
    ['command', 'connection', 'origin', 'meta'])

PostChunkMsg = collections.namedtuple(
    'PostChunkMsg',
    ['command', 'connection', 'origin', 'is_last', 'bytes', 'checksum'])

# Messages send by server

UploadApprovedMsg = collections.namedtuple(
    'UploadApprovedMsg',
    ['command', 'credit', 'chunksize', 'max_credit'])

UploadFinishedMsg = collections.namedtuple(
    'UploadFinishedMsg',
    ['command', 'upload_id'])

TransferCreditMsg = collections.namedtuple(
    'TransferCreditMsg',
    ['command', 'amount'])


class InvalidMessageError(Exception):
    def __init__(self, message, origin):
        super().__init__(self, message)
        self.origin = origin


def recv_msg_server(socket):
    frames = socket.recv_multipart(copy=False)
    assert len(frames) >= 2
    connection = frames[0].bytes
    origin = None  # TODO
    command = frames[1].bytes
    if command == b"post-file":
        assert len(frames) == 3
        try:
            meta = json.loads(frames[2].bytes.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Invalid post message")
        return PostFileMsg(command, connection, origin, meta)
    elif command == b"post-chunk":
        assert len(frames) >= 4
        is_last = int.from_bytes(frames[2].bytes, 'little') == 1
        data_bytes = frames[3].bytes
        if is_last:
            assert len(frames) == 5
            checksum = frames[4].bytes
        else:
            assert len(frames) == 4
            checksum = None
        return PostChunkMsg(command, connection, origin,
                            is_last, data_bytes, checksum)
    elif command == b"error":
        assert len(frames) == 4
        code = int.from_bytes(frames[2].bytes, 'little')
        msg = frames[3].bytes.decode()
        return ErrorMsg(command, connection, origin, code, msg)
    else:
        raise ValueError("Invalid message command: %s" % command)


class ServerConnection:
    def __init__(self, socket, connection_id):
        self._socket = socket
        self._connection_id = connection_id

    def send_upload_approved(self, chunksize, max_credit, credit):
        self._socket.send_multipart((
            self._connection_id,
            b"upload-approved",
            credit.to_bytes(4, 'little'),
            chunksize.to_bytes(4, 'little'),
            max_credit.to_bytes(4, 'little')))

    def send_upload_finished(self, upload_id):
        self._socket.send_multipart((
            self._connection_id,
            b"upload-finished",
            upload_id.encode()))

    def send_tranfer_credit(self, amount):
        self._socket.send_multipart((
            self._connection_id,
            b"transfer-credit",
            amount.to_bytes(4, 'little')))

    def send_error(self, code, msg):
        self._socket.send_multipart((
            self._connection_id,
            b"error",
            code.to_bytes(4, 'little'),
            msg.encode()))


class ClientConnection:
    def __init__(self, socket):
        self._socket = socket

    def send_post_chunk(self, data, is_last=False, checksum=None):
        if is_last:
            assert checksum is not None
            flags = 1
            self._socket.send_multipart((
                b"post-chunk",
                flags.to_bytes(4, 'little'),
                data,
                checksum))
        else:
            assert checksum is None
            flags = 0
            self._socket.send_multipart((
                b"post-chunk",
                flags.to_bytes(4, 'little'),
                data))

    def send_error(self, code, msg):
        self._socket.send_multipart((
            b"error",
            code.to_bytes(4, 'little'),
            msg.encode()))

    def send_post_file(self, name, meta):
        meta = json.dumps(meta).encode()
        self._socket.send_multipart((
            b"post-file",
            name.encode(),
            meta))
