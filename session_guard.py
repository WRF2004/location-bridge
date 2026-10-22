"""Serialize device restoration with a surviving worker after parent failure."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import time


@contextmanager
def session_lock(data_dir, timeout=75):
    path = Path(data_dir) / "device-session.lock"
    with path.open("a") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("上一定位会话仍在清理，请稍候再恢复。")
                time.sleep(0.1)
        try:
            yield handle
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
