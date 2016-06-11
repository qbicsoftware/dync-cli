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

    def cancel(self):
        log.info("Upload %s: Canceling upload.", self._id)
        self._canceled = True
        self._file.cleanup()


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
        self._debt += init_credit

        file = self._storage.add_file(msg.meta, msg.origin)

        try:
            conn = ServerConnection(self._socket, msg.connection)
            upload = Upload(conn, file, msg.origin, init_credit)
        except Exception:
            file.cleanup()
            raise
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
        for connection, upload in self._uploads.items():
            if upload.seconds_since_active() > TIMEOUT:
                cancel.append(connection)
                upload.cancel()
        for key in cancel:
            del self._uploads[key]

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
                try:
                    self._dispatch_connection(msg)
                except Exception:
                    log.exception(
                        "Exception while dispatiching message from %s",
                        msg.origin
                    )


if __name__ == '__main__':
    ctx = zmq.Context()

    path = "/tmp/dataserv"
    address = "tcp://127.0.0.1:8889"

    with Storage(path) as storage:
        log.info("Starting server")
        server = Server(ctx, storage, address)
        try:
            server.serve()
        except Exception:
            log.critical("Server shut down due to error:", exc_info=True)
        log.info("Server stopped.")
