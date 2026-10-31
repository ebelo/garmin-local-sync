"""Private file and JSON utilities."""

import base64
import datetime as dt
import json
import os
import tempfile
from pathlib import Path

from .config import UTC


def now():
    return dt.datetime.now(UTC).isoformat()


def dumps(value):
    def encode(x):
        if isinstance(x, (dt.datetime, dt.date, dt.time)):
            return x.isoformat()
        if isinstance(x, bytes):
            return {"base64": base64.b64encode(x).decode()}
        raise TypeError(type(x).__name__)

    return json.dumps(value, default=encode, separators=(",", ":"), allow_nan=False)


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
