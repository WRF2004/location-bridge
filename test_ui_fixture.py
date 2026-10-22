"""Local UI test backend: fake device CLI; never connect to a real device."""
import json
import os
import signal
import threading
from http.server import ThreadingHTTPServer
import server
from test_bridge import FakeDevice, WorkerLifecycleTests


def main():
    fixture = WorkerLifecycleTests()
    fixture.setUp()
    os.environ.update(fixture.env)
    bridge = server.Bridge(fixture.data, FakeDevice())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(bridge, "ui-test", "unused", "unused"))
    authority = f"127.0.0.1:{httpd.server_port}"
    url = f"http://{authority}/#token=ui-test"
    httpd.RequestHandlerClass = server.handler_for(bridge, "ui-test", authority, url)
    bridge.shutdown_callback = httpd.shutdown
    def shutdown(*_):
        threading.Thread(target=httpd.shutdown, daemon=True).start()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown)
    print(json.dumps({"url": url}), flush=True)
    try:
        httpd.serve_forever(poll_interval=.1)
    finally:
        bridge.close()
        httpd.server_close()
        fixture.tearDown()


if __name__ == "__main__":
    main()
