import json
import hashlib
import argparse
import collections
import os
import sys
import logging
import uuid

import zmq
import zmq.auth
from unittest import mock
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from .messages import ClientConnection, recv_msg_client


log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, stream=sys.stderr)


RETRIES = 120
RCVTIMEO = 30000


def arg_parser():
    parser = argparse.ArgumentParser(
        description="Send files and metadata to a remote server")
    parser.add_argument(
        "-m", "--meta", type=str, default=None,
        help="Path to a json file containing metadata.")
    parser.add_argument(
        "-n", "--name", help="Overwrite destination file name.")
    parser.add_argument("-k", "--key-value", action='append', default=[],
                        help="Colon seperated key-value pair. "
                        "Overwrites matedata.")
    parser.add_argument("server")
    parser.add_argument("file", default='-', nargs='?')
    return parser


def parse_args(args=None):
    parser = arg_parser()
    args = parser.parse_args(args=args)
    key_value = {}
    for keyval in args.key_value:
        try:
            key, value = keyval.split(':', 1)
        except ValueError:
            parser.error("Invalid key-value pair. Must be seperated by ':'")
        key_value[key] = value

    if args.meta is not None:
        try:
            with open(args.meta) as file:
                args.meta = json.load(file)
        except json.JSONDecodeError as e:
            parser.error("Invalid json in metadata file: " + str(e))
        except OSError as e:
            parser.error("Could not open metadata file: " + str(e))
        if not isinstance(args.meta, collections.Mapping):
            parser.error("Invalid json metadata. Must contain an object.")

    else:
        args.meta = {}
    args.meta.update(key_value)

    if args.name is None:
        if args.file == '-':
            raise parser.error(
                "Filename not known. Set it explicitly with --name")
        args.name = args.file

    args.server = "tcp://{}:8889".format(args.server)
    return args


class UploadFile:
    def __init__(self, fileobj, maxqueue, chunksize):
        self._chunksize = chunksize
        self._hasher = hashlib.sha256()
        self._seek_read = 0
        self._seek = 0
        self._chunks = collections.deque(maxlen=maxqueue)
        self._file = fileobj

    def read(self):
        if self._seek == self._seek_read:
            data = self._file.read(self._chunksize)
            self._hasher.update(data)
            self._chunks.append((self._seek, data))
            self._seek_read += len(data)
            self._seek += len(data)
            return data
        else:
            for seek, data in self._chunks:
                if seek == self._seek:
                    self._seek += len(data)
                    return data
            else:
                raise RuntimeError("Could not find requested chunk.")

    def seek(self, new_value=None):
        if new_value is None:
            return self._seek
        else:
            assert new_value <= self._seek_read
            self._seek = new_value


class Upload:
    def __init__(self, ctx, address, meta, file,
                 serverkey, pk, sk, filesize=None, progress=False):
        self._file = file
        self._socket = ctx.socket(zmq.DEALER)
        self._socket.set(zmq.LINGER, 100)
        self._socket.curve_secretkey = sk
        self._socket.curve_publickey = pk
        self._socket.curve_serverkey = serverkey
        self._socket.set(zmq.IDENTITY, uuid.uuid4().bytes)
        self._socket.set(zmq.RCVTIMEO, RCVTIMEO)
        self._socket.connect(address)
        self._conn = ClientConnection(self._socket)
        self._conn.send_post_file("filename", meta)
        msg = recv_msg_client(self._socket)
        if msg.command == b"error":
            assert False
        elif msg.command == b"upload-approved":
            self._credit = msg.credit

        if progress and tqdm is not None:
            self._progress = tqdm(
                unit='B', total=filesize, unit_scale=True, ascii=True)
        else:
            self._progress = mock.MagicMock()

        self._file = UploadFile(file, msg.max_credit, msg.chunksize)

    def send_chunks(self):
        is_last = False
        while self._credit and not is_last:
            is_last = self._send_chunk()
            self._credit -= 1

    def serve(self):
        try:
            self.send_chunks()

            while True:
                finished, upload_id = self._recv_server_status()
                if finished:
                    self._progress.close()
                    return upload_id
                self.send_chunks()
        except (KeyboardInterrupt, SystemExit):
            self._conn.send_error(400, "Client shutting down")
            raise

    def _recv_server_status(self):
        for _ in range(RETRIES):
            try:
                msg = recv_msg_client(self._socket)
            except zmq.Again:
                log.debug("Sending status query")
                self._conn.send_query_status()
            else:
                break
        else:
            raise RuntimeError("Connection timed out")

        if msg.command == b'error':
            raise RuntimeError("Server report error: " + msg.msg)
        elif msg.command == b'transfer-credit':
            log.debug("Got credit. Amount %s, old credit was %s",
                      msg.amount, self._credit)
            self._credit += msg.amount
            return False, None
        elif msg.command == b"status-report":
            log.debug("Got status report: credit: %s, seek: %s",
                      msg.credit, msg.seek)
            self._credit = msg.credit
            self._file.seek(msg.seek)
            return False, None
        elif msg.command == b"upload-finished":
            return True, msg.upload_id
        else:
            raise RuntimeError("Invalid message from server: %s" % msg.command)

    def _send_chunk(self):
        seek = self._file.seek()
        data = self._file.read()
        self._progress.update(len(data))
        is_last = not data
        if is_last:
            checksum = self._file._hasher.digest()
        else:
            checksum = None
        self._conn.send_post_chunk(seek, data, is_last, checksum)
        return is_last


def send_file(file, server_addr, meta, server_pk, pk, sk,
              filesize=None, progress=False):
    ctx = zmq.Context.instance()
    return Upload(ctx, server_addr, meta, file, server_pk, pk, sk,
                  filesize=filesize, progress=progress).serve()


def main():
    args = parse_args()

    server_key = os.path.expanduser("~/.dync/server.key")
    client_key = os.path.expanduser("~/.dync/client.key_secret")

    server_pk, _ = zmq.auth.load_certificate(server_key)
    pk, sk = zmq.auth.load_certificate(client_key)

    progress = sys.stderr.isatty()

    if args.file == '-':
        send_file(sys.stdin.buffer, args.server, args.meta, server_pk, pk, sk,
                  filesize=None, progress=progress)
    else:
        filesize = os.stat(args.file).st_size
        with open(args.file, 'rb') as source:
            send_file(source, args.server, args.meta,
                      server_pk, pk, sk, filesize, progress)


if __name__ == '__main__':
    main()
