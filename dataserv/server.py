import uuid
import collections
import logging
import sys
import time

import zmq

from .messages import InvalidMessageError, ServerConnection, recv_msg_server
from .storage import Storage

log = logging.getLogger(__name__)
logging.basicConfig(stream=sys.stderr, level=logging.INFO)


CHUNKSIZE = 16 * 1024
TIMEOUT = 3600

MAX_DEBT = 50
MIN_DEBT = 30
MAX_CREDIT = 10
TRANSFER_THRESHOLD = 3


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
            elif msg.command == b"ping":
                return self._handle_ping(msg)
            else:
                credit = self.cancel(400, "Unknown command.")
                return True, credit
        except Exception:
            log.exception("Error while handeling message")
            credit = self.cancel(500, "Unknown error")
            return True, credit

    def _handle_post_chunk(self, msg):
        assert msg.command == b"post-chunk"
        log.debug("Upload %s: Received chunk with size %s, is_last is %s",
                  self._id, len(msg.bytes), msg.is_last)

        if msg.is_last:
            log.debug("Upload %s: Last chunk received.", self._id)
            returned_credit = self._credit
            log.debug("Upload %s: Remote checksum: %s",
                      self._id, msg.checksum.hex())
            ok, local_checksum = self._file.finalize(msg.checksum)
            if ok:
                log.info("Upload %s: Upload finished successfully", self._id)
                self._conn.send_upload_finished(self._id)
            else:
                log.warn("Upload %s: Upload failed.", self._id)
                self._conn.send_error(code=500, msg="Checksum mismatch")
        else:
            self._file.write(msg.bytes)
            returned_credit = 1

        self._credit -= returned_credit
        log.debug("Upload %s: Returning credit: %s", self._id, returned_credit)
        return msg.is_last, returned_credit

    def _handle_error(self, msg):
        log.warn("Got remote error with code %s and message %s",
                 msg.code, msg.msg)
        self.silent_cancel()

    def _handle_ping(self, msg):
        self._conn.send_pong()

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
        return self._last_active - time.time()

    def _silent_cancel(self):
        self._canceled = True
        self._file.cleanup()
        return self._credit

    def cancel(self, code, msg):
        log.info("Upload %s: Canceling upload with code %s and message %s",
                 self._id, code, msg)
        self._conn.send_error(code, msg)
        return self._silent_cancel()


class Server:
    def __init__(self, ctx, storage, address):
        self._socket = ctx.socket(zmq.ROUTER)
        self._socket.bind(address)
        self._storage = storage
        self._uploads = collections.OrderedDict()
        self._debt = 0
        self._last_active_check = time.time()

    def _add_upload(self, msg):
        if msg.connection in self._uploads:
            raise ValueError("Connection id not unique")

        init_credit = min(MAX_CREDIT, max(0, MAX_DEBT - self._debt))

        file = self._storage.add_file(msg.meta, msg.origin)

        try:
            conn = ServerConnection(self._socket, msg.connection)
            upload = Upload(conn, file, msg.origin, init_credit)
        except Exception:
            file.cleanup()
            raise
        self._debt += init_credit
        self._uploads[msg.connection] = upload

    def _dispatch_connection(self, msg):
        try:
            upload = self._uploads[msg.connection]
        except KeyError:
            log.error("Invalid message from %s: no matching connection %s",
                      msg.origin, msg.connection)
            return
        if upload.origin != msg.origin:
            log.error("Got message from %s with invalid origin %s",
                      msg.origin, session_origin)
            return
        finished, returned_credit = upload.handle_msg(msg)
        self._debt -= returned_credit
        if finished:
            del self._uploads[msg.connection]

    def _distribute_credit(self):
        log.debug("Distribute credit. Current debt is %s", self._debt)
        for upload in self._uploads.values():
            if self._debt >= MAX_DEBT:
                break

            self._debt += upload.offer_credit(MAX_DEBT - self._debt)

    def _check_timeouts(self):
        cancel = []
        credit = 0
        for connection, upload in self._uploads.items():
            if upload.seconds_since_active() > TIMEOUT:
                cancel.append(connection)
                credit += upload.cancel(408, "Connection timed out.")
        for key in cancel:
            del self._uploads[key]

        self._debt -= credit

    def serve(self):
        while True:
            if self._debt < MIN_DEBT:
                try:
                    self._distribute_credit()
                except Exception:
                    log.exception("Error while distributing credit.")
            if time.time() - self._last_active_check > TIMEOUT:
                self._check_timeouts()
            try:
                log.debug("Waiting for message. Active uploads: %s, debt: %s",
                          len(self._uploads), self._debt)
                msg = recv_msg_server(self._socket)
            except InvalidMessageError as e:
                log.warn("Received invalid message from %s: %s",
                         e.origin, str(e))
                continue
            except Exception:
                log.exception("Could not read message.")
                continue
            if msg.command == b"post-file":
                try:
                    log.info("Creating new upload. Current number of "
                             "uploads: %s, current debt: %s",
                             len(self._uploads), self._debt)
                    self._add_upload(msg)
                except Exception:
                    log.exception("Exception while creating new upload.")
                    try:
                        self._socket.send_multipart((
                            msg.connection,
                            b"error",
                            (500).to_bytes(4, 'little'),
                            b"Failed to create new upload"))
                    except Exception:
                        log.exception("Could not send error message to client")
            else:
                self._dispatch_connection(msg)

    def __enter__(self):
        return self

    def __exit__(self, etype, evalue, trace):
        print(etype)
        if issubclass(etype, Exception):
            log.critical("Server shutting down due to error: ", str(evalue))
        if issubclass(etype, (KeyboardInterrupt, SystemExit)):
            log.info("Shutting down.")
        for upload in self._uploads:
            try:
                upload.cancel(503, "Server shutdown")
            except Exception:
                log.exception("Error while canceling upload")


if __name__ == '__main__':
    ctx = zmq.Context()

    path = "/tmp/dataserv"
    address = "tcp://127.0.0.1:8889"

    with Storage(path) as storage:
        log.info("Starting server")
        try:
            with Server(ctx, storage, address) as server:
                server.serve()
        except (KeyboardInterrupt, SystemExit):
            log.info("Server stopped.")
