import uuid
import collections
import logging
import sys
import time
import os
import binascii

import zmq

from .messages import InvalidMessageError, ServerConnection, recv_msg_server
from .storage import Storage
from .auth import Authenticator, ThreadAuthenticator

log = logging.getLogger(__name__)
logging.basicConfig(stream=sys.stderr, level=logging.INFO)


CHUNKSIZE = 120 * 1024
TIMEOUT = 3600

MAX_DEBT = 500
MIN_DEBT = 300
MAX_CREDIT = 200
TRANSFER_THRESHOLD = 100


class Upload:
    def __init__(self, connection, target_file, origin, init_credit):
        self._id = uuid.uuid4().hex
        log.info("Upload %s: Initialize with credit %s", self._id, init_credit)
        self.origin = origin
        self._file = target_file
        self._conn = connection
        self._credit = init_credit
        self._last_active = time.time()
        self._canceled = False
        self._conn.send_upload_approved(CHUNKSIZE, MAX_CREDIT, init_credit)

    def handle_msg(self, msg):
        assert not self._canceled
        self._last_active = time.time()
        try:
            if msg.command == b"post-chunk":
                return self._handle_post_chunk(msg)
            elif msg.command == b"error":
                return self._handle_error(msg)
            elif msg.command == b"query-status":
                return self._handle_query_status(msg)
            else:
                log.error("Ignoring unexpected message in Upload.")
                return True, 0
        except Exception:
            log.exception("Error while handeling message")
            credit = self.cancel(500, "Unknown error")
            return True, credit

    def _handle_post_chunk(self, msg):
        assert msg.command == b"post-chunk"
        log.debug("Upload %s: Received chunk with size %s, is_last is %s",
                  self._id, len(msg.data), msg.is_last)
        if msg.seek != self._file.nbytes_written:
            log.debug("Upload %s: Invalid chunk, seek is incorrect", self._id)
            return False, 0

        if msg.is_last:
            log.debug("Upload %s: Last chunk received.", self._id)
            returned_credit = self._credit
            log.debug("Upload %s: Remote checksum: %s",
                      self._id, binascii.hexlify(msg.checksum).decode())
            try:
                self._file.finalize(msg.checksum)
            except Exception as e:
                log.warn("Upload %s: Upload failed.", self._id)
                self._conn.send_error(code=500, msg=str(e))
            else:
                log.info("Upload %s: Upload finished successfully", self._id)
                self._conn.send_upload_finished(self._id)
        else:
            self._file.write(msg.data)
            returned_credit = 1

        self._credit -= returned_credit
        log.debug("Upload %s: Returning credit: %s", self._id, returned_credit)
        return msg.is_last, returned_credit

    def _handle_error(self, msg):
        log.warn("Got remote error with code %s and message %s",
                 msg.code, msg.msg)
        self._silent_cancel()
        return True, self._credit

    def _handle_query_status(self, msg):
        log.debug("Upload %s: Client is querying status.", self._id)
        self._conn.send_status_report(self._file.nbytes_written, self._credit)
        return False, 0

    def offer_credit(self, amount):
        log.debug("Upload %s: Offered credit: %s. Current credit is %s",
                  self._id, amount, self._credit)
        if self._credit >= TRANSFER_THRESHOLD:
            return 0

        old = self._credit
        self._credit = min(MAX_CREDIT, self._credit + amount)
        transfer = self._credit - old
        log.debug("Upload %s: Transfering credit: %s", self._id, transfer)
        self._conn.send_tranfer_credit(transfer)
        return transfer

    def seconds_since_active(self):
        return time.time() - self._last_active

    def _silent_cancel(self):
        self._canceled = True
        self._file.abort()
        return self._credit

    def cancel(self, code, msg):
        log.info("Upload %s: Canceling upload with code %s and message %s",
                 self._id, code, msg)
        self._conn.send_error(code, msg)
        return self._silent_cancel()


class Server:
    def __init__(self, ctx, storage, address, server_keys):
        self._socket = ctx.socket(zmq.ROUTER)
        self._socket.curve_secretkey = server_keys[1]
        self._socket.curve_publickey = server_keys[0]
        self._socket.curve_server = True
        self._socket.set(zmq.ROUTER_HANDOVER, 1)
        self._socket.bind(address)
        self._storage = storage
        self._uploads = collections.OrderedDict()
        self._debt = 0
        self._last_active_check = time.time()

    def _add_upload(self, msg):
        log.info("Creating new upload.")
        if msg.connection in self._uploads:
            raise ValueError("Connection id not unique")

        init_credit = min(MAX_CREDIT, max(0, MAX_DEBT - self._debt))

        file = self._storage.add_file(msg.name, msg.meta, msg.origin)

        try:
            conn = ServerConnection(self._socket, msg.connection)
            upload = Upload(conn, file, msg.origin, init_credit)
        except Exception:
            file.abort()
            raise
        self._debt += init_credit
        self._uploads[msg.connection] = upload

    def _dispatch_connection(self, msg):
        try:
            upload = self._uploads[msg.connection]
        except KeyError:
            log.warn("%s message from %s, but no matching connection %s",
                     msg.command[:20], msg.origin,
                     binascii.hexlify(msg.connection).decode()[:20])
            self.send_error(msg.connection, 400, "Unknown connection.")
            return
        if upload.origin != msg.origin:
            log.error("Got message from %s with invalid origin %s",
                      msg.origin, upload.origin)
            return
        finished, returned_credit = upload.handle_msg(msg)
        self._debt -= returned_credit
        if finished:
            del self._uploads[msg.connection]
            log.info("Upload finished. %s remaining", len(self._uploads))

    def _distribute_credit(self):
        log.debug("Distribute credit. Current debt is %s", self._debt)
        for upload in self._uploads.values():
            if self._debt >= MAX_DEBT:
                break

            self._debt += upload.offer_credit(MAX_DEBT - self._debt)

    def _check_timeouts(self):
        self._last_active_check = time.time()
        cancel = []
        credit = 0
        for connection, upload in self._uploads.items():
            if upload.seconds_since_active() > TIMEOUT:
                cancel.append(connection)
                credit += upload.cancel(408, "Connection timed out.")
        for key in cancel:
            del self._uploads[key]

        self._debt -= credit

    def send_error(self, connection_id, code=500, msg=""):
        try:
            self._socket.send_multipart((
                connection_id,
                b"error",
                (500).to_bytes(4, 'big'),
                msg.encode('utf8')))
        except Exception:
            log.exception("Could not send error message to client")

    def log_status(self):
        log.info("Current number of uploads: %s, current debt: %s",
                 len(self._uploads), self._debt)

    def serve(self):
        while True:
            if self._debt < MIN_DEBT:
                self._distribute_credit()
            if time.time() - self._last_active_check > TIMEOUT:
                self._check_timeouts()
                self.log_status()
            log.debug("Waiting for message. Active uploads: %s, debt: %s",
                      len(self._uploads), self._debt)
            try:
                msg = recv_msg_server(self._socket)
            except (InvalidMessageError, OverflowError) as e:
                log.debug("Invalid message from %s: %s", e.origin, str(e))
                if e.connection_id is not None:
                    self.send_error(e.connection_id, 400, "Invalid message")
                continue
            if msg.command == b"post-file":
                try:
                    self._add_upload(msg)
                except Exception:
                    log.exception("Exception while creating new upload.")
                    self.send_error(
                        msg.connection, 500, "Failed to create upload")
                else:
                    self.log_status()
            else:
                self._dispatch_connection(msg)

    def __enter__(self):
        return self

    def __exit__(self, etype, evalue, trace):
        if issubclass(etype, Exception):
            log.critical("Server shutting down due to error: %s", str(evalue))
        if issubclass(etype, (KeyboardInterrupt, SystemExit)):
            log.info("Shutting down.")
        for upload in self._uploads.values():
            try:
                upload.cancel(503, "Server shutdown")
            except Exception:
                log.exception("Error while canceling upload")


def prepare_auth(ctx, keydir):
    certdir = os.path.join(keydir, 'clients')
    servercert = os.path.join(keydir, 'server.key_secret')

    if not (os.path.exists(keydir) and
            os.path.exists(certdir) and
            os.path.exists(servercert)):
        raise ValueError("Unable to start server: Could not find certificates")

    auth = Authenticator(ctx)
    auth.configure_curve(domain="*", location=certdir)
    auth = ThreadAuthenticator(ctx, authenticator=auth)
    auth.start()
    server_keys = zmq.auth.load_certificate(servercert)
    return auth, server_keys


def main():
    ctx = zmq.Context()

    try:
        auth, server_keys = prepare_auth(ctx, 'certificates')
    except Exception:
        log.critical("Failed to load keys", exc_info=True)
        return 1

    path = "/tmp/dataserv"
    address = "tcp://127.0.0.1:8889"

    with Storage(path) as storage:
        log.info("Starting server")
        try:
            with Server(ctx, storage, address, server_keys) as server:
                server.serve()
        except KeyboardInterrupt:
            pass
        finally:
            auth.stop()
            log.info("Server stopped.")


if __name__ == '__main__':
    main()
