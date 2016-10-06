"""Generic linux daemon base class for python 3.x."""

import sys
import os
import time
import atexit
import signal


class Daemon:
    """A generic daemon class.

    Usage: subclass the daemon class and override the run() method."""

    def __init__(self, pidfile, umask):
        self._umask = umask
        self._pidfile = pidfile

    def daemonize(self):
        """Deamonize class. UNIX double fork mechanism."""

        try:
            pid = os.fork()
            if pid > 0:
                # exit first parent
                sys.exit(0)
        except OSError as err:
            sys.stderr.write('fork #1 failed: {0}\n'.format(err))
            sys.exit(1)

        # decouple from parent environment
        os.chdir('/')
        os.setsid()
        os.umask(self._umask)

        # do second fork
        try:
            pid = os.fork()
            if pid > 0:
                # exit from second parent
                sys.exit(0)
        except OSError as err:
            sys.stderr.write('fork #2 failed: {0}\n'.format(err))
            sys.exit(1)

        # redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()

        # write pidfile
        atexit.register(self.delpid)

        pid = str(os.getpid())
        with open(self._pidfile, 'x') as f:
            f.write(pid + '\n')

    def delpid(self):
        os.remove(self._pidfile)

    def start(self, fun, arg):
        """Start the daemon."""

        # Check for a pidfile to see if the daemon already runs
        try:
            with open(self._pidfile, 'r') as pf:
                pid = int(pf.read().strip())
        except IOError:
            pid = None

        if pid:
            message = "pidfile {0} already exist. " + \
                      "Daemon already running?\n"
            sys.stderr.write(message.format(self._pidfile))
            sys.exit(1)

        # Start the daemon
        self.daemonize()
        self.run(fun, arg)

    def stop(self):
        """Stop the daemon."""

        # Get the pid from the pidfile
        try:
            with open(self._pidfile, 'r') as pf:
                    pid = int(pf.read().strip())
        except FileNotFoundError:
            pid = None

        if pid is None:
            message = "pidfile {0} does not exist. " + \
                      "Daemon not running?\n"
            sys.stderr.write(message.format(self._pidfile))
            return  # not an error in a restart

        # Try killing the daemon process
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            try:
                self.delpid()
            except FileNotFoundError:
                pass
            except PermissionError:
                print("Could not remove pidfile. Permission denied.")
            return

        time_passed = 0
        while time_passed < 10:
            time.sleep(0.1)
            time_passed += 0.1
            if not check_process(pid):
                break
        else:
            print("Deamon does not shutdown on SIGTERM, killing it...",
                  file=sys.stderr)
            os.kill(pid, signal.SIGKILL)

        try:
            self.delpid()
        except FileNotFoundError:
            pass
        except PermissionError:
            print("Could not remove pidfile. Permission denied.")

    def restart(self, fun, arg):
        """Restart the daemon."""
        self.stop()
        self.start(fun, arg)

    def run(self, fun, arg):
        """You should override this method when you subclass Daemon.

        It will be called after the process has been daemonized by
        start() or restart()."""


class DyncDaemon(Daemon):

    def run(self, fun, arg):
        fun(arg)


def check_process(pid):
    """Check if process is running"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    else:
        return True
