import json
import hashlib
import argparse
import collections
import os

import zmq
import zmq.auth
from unittest import mock
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from .messages import ClientConnection, recv_msg_client


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


class UploadFile:
    def __init__(self, fileobj, maxqueue, chunksize):
        self._chunksize = chunksize
        self._hasher = hashlib.sha256()
        self._chunks_read = 0
        self.chunk_seek = 0
        self._chunks = collections.deque(maxlen=maxqueue)
        self._file = fileobj

    def read(self):
        if self.chunk_seek == self._chunks_read:
            data = self._file.read(self._chunksize)
            self._hasher.update(data)
            self._chunks.append(data)
            self._chunks_read += 1
            self.chunk_seek += 1
            return data
        else:
            nback = self._chunks_read - self.chunk_seek
            data = self._chunks[-nback]
            self.chunk_seek += 1
            return data

    def seek_chunk(self, chunk):
        self.chunk_seek = chunk


class Upload:
    def __init__(self, ctx, address, meta, file,
                 serverkey, pk, sk, filesize=None, progress=False):
        self._file = file
        self._socket = ctx.socket(zmq.DEALER)
        self._socket.set(zmq.LINGER, 100)
        self._socket.curve_secretkey = sk
        self._socket.curve_publickey = pk
        self._socket.curve_serverkey = serverkey
        self._socket.connect(address)
        self._conn = ClientConnection(self._socket)
        self._conn.send_post_file("filename", meta)
        msg = recv_msg_client(self._socket)
        if msg.command == b"error":
            assert False
        elif msg.command == b"upload-approved":
            self._credit = msg.credit

        if progress and tqdm is not None:
            self._progress = tqdm(unit='B', total=filesize, unit_scale=True)
        else:
            self._progress = mock.MagicMock()

        self._file = UploadFile(file, msg.max_credit, msg.chunksize)

    def send_chunks(self):
        is_last = False
        while self._credit and not is_last:
            is_last = self._send_chunk()
            self._credit -= 1

    def serve(self):
        self.send_chunks()

        while True:
            msg = recv_msg_client(self._socket)
            if msg.command == b'error':
                raise RuntimeError(msg.msg)
            elif msg.command == b'transfer-credit':
                self._credit += msg.amount
                self.send_chunks()
            elif msg.command == b"seek":
                raise NotImplemented()
            elif msg.command == b"upload-finished":
                self._progress.close()
                return msg.upload_id

    def _send_chunk(self):
        data = self._file.read()
        self._progress.update(len(data))
        nchunk = self._file.chunk_seek - 1
        is_last = not data
        if is_last:
            checksum = self._file._hasher.digest()
        else:
            checksum = None
        self._conn.send_post_chunk(nchunk, data, is_last, checksum)
        return is_last


def send_file(file, server_addr, meta, server_pk, pk, sk,
              filesize=None, progress=False):
    ctx = zmq.Context.instance()
    return Upload(ctx, server_addr, meta, file, server_pk, pk, sk,
                  filesize=filesize, progress=progress).serve()


def main():
    args = parse_args()

    server_pk, _ = zmq.auth.load_certificate("./certificates/server.key")
    pk, sk = zmq.auth.load_certificate("./certificates/client.key_secret")

    if args.meta:
        with open(args.meta) as meta:
            meta = json.load(meta)
    else:
        meta = {}

    if args.source == '-':
        send_file(sys.stdin.buffer, args.server, meta, server_pk, pk, sk)
    else:
        filesize = os.stat(args.source).st_size
        with open(args.source, 'rb') as source:
            send_file(source, args.server, meta,
                      server_pk, pk, sk, filesize, True)


if __name__ == '__main__':
    main()
