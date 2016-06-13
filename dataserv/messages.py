import collections
import json


ErrorMsg = collections.namedtuple(
    'ErrorMsg',
    ['command', 'connection', 'origin', 'code', 'msg'])

# Messages send by client

PostFileMsg = collections.namedtuple(
    'PostFileMsg',
    ['command', 'connection', 'origin', 'name', 'meta'])

PostChunkMsg = collections.namedtuple(
    'PostChunkMsg',
    ['command', 'connection', 'origin', 'is_last', 'seek', 'data', 'checksum'])

PingMsg = collections.namedtuple(
    'PingMsg',
    ['command', 'connection', 'origin'])

QueryStatusMsg = collections.namedtuple(
    'StatusQueryMsg',
    ['command', 'connection', 'origin'])

# Messages send by server

UploadApprovedMsg = collections.namedtuple(
    'UploadApprovedMsg',
    ['command', 'credit', 'chunksize', 'max_credit'])

UploadFinishedMsg = collections.namedtuple(
    'UploadFinishedMsg',
    ['command', 'upload_id'])

TransferCreditMsg = collections.namedtuple(
    'TransferCreditMsg',
    ['command', 'amount', 'ack_chunks'])

PongMsg = collections.namedtuple(
    'PongMsg',
    ['command'])

StatusMsg = collections.namedtuple(
    'StatusMsg',
    ['command', 'last_active', 'nbytes', 'credit'])


class InvalidMessageError(Exception):
    def __init__(self, desc, origin=None, connection_id=None):
        super().__init__(desc)
        self.connection_id = connection_id
        self.origin = origin


def check_len(frames, num, id_=None):
    if len(frames) < num:
        raise InvalidMessageError(
            "Unexpected number of frames. Need at least %s but got %s" %
            (num, len(frames)), connection_id=id_)


def recv_msg_client(socket):
    frames = socket.recv_multipart(copy=False)
    if len(frames) == 0:
        raise InvalidMessageError("Unexpected number of frames.")
    command = frames[0].bytes
    if command == b'error':
        check_len(frames, 3)
        code = int.from_bytes(frames[1].bytes, 'little')
        msg = frames[2].bytes.decode()
        return ErrorMsg(command, None, None, code, msg)
    elif command == b"pong":
        check_len(frames, 1)
        return PongMsg(command)
    elif command == b"transfer-credit":
        check_len(frames, 3)
        amount = int.from_bytes(frames[1].bytes, 'little')
        ack_chunks = int.from_bytes(frames[2].bytes, 'little')
        return TransferCreditMsg(command, amount, ack_chunks)
    elif command == b"upload-approved":
        check_len(frames, 4)
        credit = int.from_bytes(frames[1].bytes, 'little')
        chunksize = int.from_bytes(frames[2].bytes, 'little')
        max_credit = int.from_bytes(frames[3].bytes, 'little')
        return UploadApprovedMsg(command, credit, chunksize, max_credit)
    elif command == b"upload-finished":
        check_len(frames, 2)
        upload_id = frames[1].bytes.decode()
        return UploadFinishedMsg(command, upload_id)
    else:
        raise InvalidMessageError("Unknown command in message")


def recv_msg_server(socket):
    frames = socket.recv_multipart(copy=False)
    if not len(frames) >= 2:
        raise InvalidMessageError("Unexpected number of frames.")
    connection = frames[0].bytes
    origin = None  # TODO
    command = frames[1].bytes
    if command == b"post-file":
        check_len(frames, 4)
        name = frames[2].bytes.decode()
        try:
            meta = json.loads(frames[3].bytes.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise InvalidMessageError("Invalid post message", connection)
        return PostFileMsg(command, connection, origin, name, meta)
    elif command == b"post-chunk":
        check_len(frames, 5, connection)
        is_last = int.from_bytes(frames[2].bytes, 'little') == 1
        seek = int.from_bytes(frames[3].bytes, 'little')
        data = frames[4].bytes
        if is_last:
            check_len(frames, 6)
            checksum = frames[5].bytes
        else:
            check_len(frames, 5)
            checksum = None
        return PostChunkMsg(command, connection, origin,
                            is_last, seek, data, checksum)
    elif command == b"error":
        check_len(frames, 4, connection)
        code = int.from_bytes(frames[2].bytes, 'little')
        msg = frames[3].bytes.decode()
        return ErrorMsg(command, connection, origin, code, msg)
    elif command == b"ping":
        check_len(frames, 2, connection)
        return PingMsg(command, connection, origin)
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

    def send_tranfer_credit(self, amount, ack_chunks):
        self._socket.send_multipart((
            self._connection_id,
            b"transfer-credit",
            amount.to_bytes(4, 'little'),
            ack_chunks.to_bytes(8, 'little')))

    def send_pong(self):
        self._socket.send_multipart((
            self._connection_id,
            b"pong"))

    def send_error(self, code, msg):
        self._socket.send_multipart((
            self._connection_id,
            b"error",
            code.to_bytes(4, 'little'),
            msg.encode()))


class ClientConnection:
    def __init__(self, socket):
        self._socket = socket

    def send_post_chunk(self, seek, data, is_last=False, checksum=None):
        if is_last:
            assert checksum is not None
            flags = 1
            self._socket.send_multipart((
                b"post-chunk",
                flags.to_bytes(4, 'little'),
                seek.to_bytes(8, 'little'),
                data,
                checksum))
        else:
            assert checksum is None
            flags = 0
            self._socket.send_multipart((
                b"post-chunk",
                flags.to_bytes(4, 'little'),
                seek.to_bytes(8, 'little'),
                data))

    def send_post_file(self, name, meta):
        meta = json.dumps(meta).encode()
        self._socket.send_multipart((
            b"post-file",
            name.encode(),
            meta))

    def send_ping(self):
        self._socket.send(b"ping")

    def send_error(self, code, msg):
        self._socket.send_multipart((
            b"error",
            code.to_bytes(4, 'little'),
            msg.encode()))
