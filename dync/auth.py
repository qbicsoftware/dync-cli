"""zeromq authenticator.

The authenticator in `zmq.auth` does not allow access to
the origin of individual messages. `zmq` itself will make
the `user_id` frame of corresponding zap resposes available
as `Frame.get(b'User-Id')`. This module makes sure we
acutally return the user id instead of the literal
'user_id'.
"""

import logging
import zmq
import zmq.auth.thread
from zmq.utils import z85
import os

VERSION = b'1.0'

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


def load_certificate(filename):
    """Load public and secret key from a zmq certificate.

    Returns (public_key, secret_key)

    If the certificate file only contains the public key,
    secret_key will be None.

    If there is no public key found in the file, ValueError will be raised.

    This is the same as `zmq.util.load_certificate`, but it will also
    return the user_id of a certificate if this is present in the
    metadata of the certificate.
    """
    public_key = None
    secret_key = None
    user_id = None
    if not os.path.exists(filename):
        raise IOError("Invalid certificate file: {0}".format(filename))

    with open(filename, 'rb') as f:
        for line in f:
            line = line.strip()
            if line.startswith(b'#'):
                continue
            if line.startswith(b'user_id'):
                user_id = line.split(b"=", 1)[1].strip(b' \t\'"')
            if line.startswith(b'public-key'):
                public_key = line.split(b"=", 1)[1].strip(b' \t\'"')
            if line.startswith(b'secret-key'):
                secret_key = line.split(b"=", 1)[1].strip(b' \t\'"')
            if public_key and secret_key:
                break

    if public_key is None:
        raise ValueError("No public key found in %s" % filename)
    return public_key, secret_key, user_id.decode()


class Authenticator:
    def __init__(self, context):
        self.context = context
        self.zap_socket = None

    def start(self):
        assert self.zap_socket is None
        self.zap_socket = self.context.socket(zmq.REP)
        self.zap_socket.linger = 1
        self.zap_socket.bind("inproc://zeromq.zap.01")

    def stop(self):
        if self.zap_socket:
            self.zap_socket.close()
        self.zap_socket = None

    def configure_curve(self, domain="*", location=None):
        self.clients = {}
        for filename in os.listdir(location):
            if filename.endswith('.key'):
                path = os.path.join(location, filename)
                public, _, user_id = load_certificate(path)
                log.debug("Allow connections from %s", user_id)
                self.clients[z85.decode(public)] = user_id

    def handle_zap_message(self, msg):
        if len(msg) != 7:
            log.error("Invalid curve ZAP message, not enough frames: %r", msg)
            if len(msg) < 2:
                log.critical("Not enough information to reply")
            else:
                self._send_zap_reply(msg[1], b"400", b"Not enough frames")
            return

        version, request_id, domain, _, _, mechanism, client_key = msg

        if version != VERSION:
            log.error("Invalid ZAP version: %r", msg)
            self._send_zap_reply(request_id, b"400", b"Invalid version")
            return

        if mechanism != b'CURVE':
            log.debug("Only CURVE authentication is supported")
            self._send_zap_reply(request_id, b"400", b"Invalid mechanism")

        user_id = self.clients.get(client_key, None)
        if user_id is None:
            log.info("Incoming connection from unknown user %s" % client_key)
            self._send_zap_reply(request_id, b"400", b"Invalid credentials")
        else:
            log.info("Incoming connection from %s" % user_id)
            self._send_zap_reply(request_id, b"200", b"OK", user_id)

    def _send_zap_reply(self, request_id, status_code, status_text, user_id=''):
        """Send a ZAP reply to finish the authentication."""
        user_id = user_id if status_code == b'200' else ''
        user_id = user_id.encode()
        metadata = b''  # not currently used
        log.debug("ZAP reply code=%s text=%s", status_code, status_text)
        reply = [VERSION, request_id, status_code, status_text, user_id, metadata]
        self.zap_socket.send_multipart(reply)


class ThreadAuthenticator(zmq.auth.thread.ThreadAuthenticator):
    def __init__(self, context, authenticator):
        super().__init__(context=context, log=log)
        self.authenticator = authenticator

    def start(self):
        """Start the authentication thread"""
        # create a socket to communicate with auth thread.
        self.pipe = self.context.socket(zmq.PAIR)
        self.pipe.linger = 1
        self.pipe.bind(self.pipe_endpoint)
        self.thread = zmq.auth.thread.AuthenticationThread(
            self.context, self.pipe_endpoint, encoding=self.encoding,
            log=self.log, authenticator=self.authenticator)
        self.thread.start()
        # TODO should wait for thread to start. See pyzmq 0bb7c10.
        # Support for this is not in stable pyzmq yet.
