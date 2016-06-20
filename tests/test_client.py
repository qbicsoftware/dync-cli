from nose.tools import assert_raises
from nose import SkipTest
import io
import sys
from dync import client


def test_parse_args():
    args = "-k a:b -k c:d localhost file".split(' ')
    args = client.parse_args(args)
    assert args.file == "file"
    assert args.name == "file"
    assert args.server == "tcp://localhost:8889"
    assert args.meta == {'a': 'b', 'c': 'd'}

    args = "localhost file".split(' ')
    args = client.parse_args(args)
    assert args.file == "file"
    assert args.name == "file"
    assert args.server == "tcp://localhost:8889"
    assert args.meta == {}


def test_parse_args_filename():
    if sys.version_info < (3, 5, 0):
        raise SkipTest
    from contextlib import redirect_stderr
    args = "localhost -".split(' ')
    with assert_raises(SystemExit):
        f = io.StringIO()
        with redirect_stderr(f):
            client.parse_args(args)
    args = "-n file localhost -".split(' ')
    args = client.parse_args(args)
    assert args.file == "-"
    assert args.name == "file"
    assert args.server == "tcp://localhost:8889"
    assert args.meta == {}
