import json
import hashlib
import argparse

import zmq


def parse_args():
    parser = argparse.ArgumentParser(
        description="Send files and metadata to a remote server")
    parser.add_argument(
        "-m", "--meta", type=str, default=None,
        help="Path to a json file containing metadata.")
    parser.add_argument(
        "-n", "--name", help="Overwrite destination file name.")
    parser.add_argument("server")
    parser.add_argument("source")
    return parser.parse_args()


class Upload:
    def __init__(self, ctx, address, meta, file):
        self._file = file
        self._socket = ctx.socket(zmq.DEALER)
        self._socket.set(zmq.LINGER, 100)
        self._socket.connect(address)
        meta = json.dumps(meta).encode()
        self._socket.send_multipart((b"post-file", meta))
        frames = self._socket.recv_multipart()
        assert frames[0] == b"upload-approved"
        self._credit = int.from_bytes(frames[1], 'little')
        self._chunksize = int.from_bytes(frames[2], 'little')
        self._maxcredit = int.from_bytes(frames[3], 'little')
        self._hasher = hashlib.sha256()

    def serve(self):
        finished = False

        while not finished:
            self._credit -= 1
            finished = self._send_chunk()
            if finished:
                return self._finalize()
            if not self._credit:
                self._wait_for_credit()

    def _send_chunk(self):
        data = self._file.read(self._chunksize)
        self._hasher.update(data)
        if not data:
            flags = (1).to_bytes(4, 'little')
            checksum = self._hasher.digest()
            self._socket.send_multipart((b"post-chunk", flags, data, checksum))
        else:
            flags = (0).to_bytes(4, 'little')
            self._socket.send_multipart((b"post-chunk", flags, data))
        return not data

    def _wait_for_credit(self):
        frames = self._socket.recv_multipart()
        assert frames[0] == b"transfer-credit"
        self._credit += int.from_bytes(frames[1], 'little')

    def _finalize(self):
        frames = self._socket.recv_multipart()
        while frames[0] == b"transfer-credit":
            frames = self._socket.recv_multipart()
        if frames[0] == b"upload-finished":
            return frames[1].decode()
        elif frames[1] == b"error":
            raise RuntimeError("Failed file upload: %s" % frames[3].decode())
        else:
            raise ValueError("Invalid remote message.")


def send_file(file, server_addr, meta):
    ctx = zmq.Context.instance()
    return Upload(ctx, address, meta, file).serve()


if __name__ == '__main__':
    args = parse_args()
    address = "tcp://127.0.0.1:8889"
    if args.meta:
        with open(args.meta) as meta:
            meta = json.load(meta)
    else:
        meta = {}

    if args.source == '-':
        send_file(sys.stdin.buffer, args.address, meta)
    else:
        with open(args.source, 'rb') as source:
            send_file(source, address, meta)
