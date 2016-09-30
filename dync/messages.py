import collections
import json
import zmq


ErrorMsg = collections.namedtuple(
    'ErrorMsg',
    ['command', 'connection', 'origin', 'code', 'msg'])

# Messages send by client

PostFileMsg = collections.namedtuple(
    'PostFileMsg',
    ['command', 'connection', 'origin', 'flags', 'name', 'meta'])

PostChunkMsg = collections.namedtuple(
    'PostChunkMsg',
    ['command', 'connection', 'origin', 'is_last', 'seek', 'data', 'checksum'])

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
    ['command', 'amount'])

StatusReportMsg = collections.namedtuple(
    'StatusReportMsg',
    ['command', 'seek', 'credit'])


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
        code = int.from_bytes(frames[1].bytes, 'big')
        msg = frames[2].bytes.decode('utf8')
        return ErrorMsg(command, None, None, code, msg)
    elif command == b"transfer-credit":
        check_len(frames, 2)
        amount = int.from_bytes(frames[1].bytes, 'big')
        return TransferCreditMsg(command, amount)
    elif command == b"upload-approved":
        check_len(frames, 4)
        credit = int.from_bytes(frames[1].bytes, 'big')
        chunksize = int.from_bytes(frames[2].bytes, 'big')
        max_credit = int.from_bytes(frames[3].bytes, 'big')
        return UploadApprovedMsg(command, credit, chunksize, max_credit)
    elif command == b"upload-finished":
        check_len(frames, 2)
        upload_id = frames[1].bytes.decode('utf8')
        return UploadFinishedMsg(command, upload_id)
    elif command == b"status-report":
        check_len(frames, 3)
        seek = int.from_bytes(frames[1].bytes, 'big')
        credit = int.from_bytes(frames[2].bytes, 'big')
        return StatusReportMsg(command, seek, credit)
    else:
        raise InvalidMessageError("Unknown command in message")


def recv_msg_server(socket):
    frames = socket.recv_multipart(copy=False)
    if not len(frames) >= 2:
        raise InvalidMessageError("Unexpected number of frames.")
    connection = frames[0].buffer
    try:
        origin = frames[1].get(b'User-Id')
    except zmq.ZMQError:
        origin = None
    if origin is not None:
        if not all(frame.get(b"User-Id") == origin for frame in frames[2:]):
            raise InvalidMessageError("Invalid message origin")
    command = frames[1].bytes
    if command == b"post-file":
        check_len(frames, 5)
        flags = int.from_bytes(frames[2].bytes, 'big')
        name = frames[3].bytes.decode('utf8')
        try:
            meta = json.loads(frames[4].bytes.decode('utf8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise InvalidMessageError("Invalid post message", connection)
        return PostFileMsg(command, connection, origin, flags, name, meta)
    elif command == b"post-chunk":
        check_len(frames, 5, connection)
        is_last = int.from_bytes(frames[2].buffer, 'big') == 1
        seek = int.from_bytes(frames[3].buffer, 'big')
        data = frames[4].buffer
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
        code = int.from_bytes(frames[2].bytes, 'big')
        msg = frames[3].bytes.decode('utf8')
        return ErrorMsg(command, connection, origin, code, msg)
    elif command == b"query-status":
        check_len(frames, 2, connection)
        return QueryStatusMsg(command, connection, origin)
    else:
        raise InvalidMessageError("Invalid message command: %s" % command)


class ServerConnection:
    def __init__(self, socket, connection_id):
        self._socket = socket
        self._connection_id = connection_id

    def send_upload_approved(self, chunksize, max_credit, credit):
        self._socket.send_multipart((
            self._connection_id,
            b"upload-approved",
            credit.to_bytes(4, 'big'),
            chunksize.to_bytes(4, 'big'),
            max_credit.to_bytes(4, 'big')))

    def send_upload_finished(self, upload_id):
        self._socket.send_multipart((
            self._connection_id,
            b"upload-finished",
            upload_id.encode('utf8')))

    def send_tranfer_credit(self, amount):
        self._socket.send_multipart((
            self._connection_id,
            b"transfer-credit",
            amount.to_bytes(4, 'big')))

    def send_status_report(self, seek, credit):
        self._socket.send_multipart((
            self._connection_id,
            b"status-report",
            seek.to_bytes(8, 'big'),
            credit.to_bytes(4, 'big')))

    def send_error(self, code, msg):
        self._socket.send_multipart((
            self._connection_id,
            b"error",
            code.to_bytes(4, 'big'),
            msg.encode('utf8')))


class ClientConnection:
    def __init__(self, socket):
        self._socket = socket

    def send_post_chunk(self, seek, data, is_last=False, checksum=None):
        if is_last:
            assert checksum is not None
            flags = 1
            self._socket.send_multipart((
                b"post-chunk",
                flags.to_bytes(4, 'big'),
                seek.to_bytes(8, 'big'),
                data,
                checksum))
        else:
            assert checksum is None
            flags = 0
            self._socket.send_multipart((
                b"post-chunk",
                flags.to_bytes(4, 'big'),
                seek.to_bytes(8, 'big'),
                data))

    def send_post_file(self, name, meta):
        meta = json.dumps(meta).encode('utf8')
        self._socket.send_multipart((
            b"post-file",
            (0).to_bytes(4, 'big'),
            name.encode('utf8'),
            meta))

    def send_query_status(self):
        self._socket.send(b"query-status")

    def send_error(self, code, msg):
        self._socket.send_multipart((
            b"error",
            code.to_bytes(4, 'big'),
            msg.encode('utf8')))


