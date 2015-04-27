import contextlib
import re
import shutil
import tempfile


class HandledError(Exception):
    def __init__(self, msg, *args, **kwargs):
        super(Exception, self).__init__()
        self.message = msg.format(*args, **kwargs)


@contextlib.contextmanager
def TemporaryDirectory():
    name = tempfile.mkdtemp()
    try:
        yield name
    finally:
        shutil.rmtree(name)


def normalize(string):
    string = string.strip()\
        .lower()\
        .replace("_", "-")

    return re.sub("[^a-z0-9.-]", "", string)
