"""Keep a manual session alive, restoring on stop, parent EOF, or termination."""
import os
import runpy
import signal
import subprocess
import sys
from session_guard import session_lock


def emit(message):
    try:
        print(message, flush=True)
    except OSError:
        # The server may have crashed and closed the output pipe.
        pass


def main():
    from pymobiledevice3.osu.os_utils import get_os_utils

    def wait_for_parent():
        emit("LOCATION_BRIDGE_SET_ACK")
        # No duration limit. EOF after server death also starts restoration.
        sys.stdin.readline()

    def stop(*_):
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    get_os_utils().wait_return = wait_for_parent
    sys.argv = ["pymobiledevice3", "developer", "dvt", "simulate-location",
                "set", "--", *sys.argv[1:]]
    with session_lock(os.environ["LOCATION_BRIDGE_DATA"]) as lock:
        try:
            runpy.run_module("pymobiledevice3", run_name="__main__")
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal.signal(sig, signal.SIG_IGN)
            try:
                # Upstream set exits without explicitly clearing. Keep this
                # cleanup independent of the parent and of the set event loop.
                result = subprocess.run(
                    [sys.executable, "-m", "pymobiledevice3", "developer", "dvt",
                     "simulate-location", "clear"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, timeout=60,
                    pass_fds=(lock.fileno(),))
                if result.returncode == 0:
                    emit("LOCATION_BRIDGE_CLEAR_ACK")
                else:
                    emit("自动恢复未完成：" + result.stdout[-1000:])
            except Exception as exc:
                emit("自动恢复未完成：" + str(exc))


if __name__ == "__main__":
    main()
