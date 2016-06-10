def send_upload_approved(socket, connection, credit):
    socket.send_multipart((
        connection,
        b"upload-approved",
        credit.to_bytes(4, 'little'),
        CHUNKSIZE.to_bytes(4, 'little'),
        MAX_CREDIT.to_bytes(4, 'little'),
    ))


def send_upload_finished(socket, connection, upload_id):
    socket.send_multipart((
        connection,
        b"upload-finished",
        upload_id.encode(),
    ))


def send_tranfer_credit(socket, connection, amount):
    socket.send_multipart((
        connection,
        b"transfer-credit",
        amount.to_bytes(4, 'little')
    ))


def send_error(socket, connection, code, msg):
    socket.send_multipart((
        connection,
        b"error",
        code.to_bytes(4, 'little'),
        msg.decode()
    ))


PostFileMsg = collections.namedtuple(
    'PostFileMsg',
    ['command', 'connection', 'origin', 'meta']
)

PostChunkMsg = collections.namedtuple(
    'PostChunkMsg',
    ['command', 'connection', 'origin', 'is_last', 'bytes', 'checksum']
)

ErrorMsg = collections.namedtuple(
    'ErrorMsg',
    ['command', 'connection', 'origin', 'code', 'msg']
)


class InvalidMessageError(Exception):
    def __init__(self, message, origin):
        super().__init__(self, message)
        self.origin = origin


def recv_msg(socket):
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
