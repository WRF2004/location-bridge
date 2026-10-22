"""Device-free tests. Fakes never send a command to a physical iPhone."""
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import server


class FakeDevice:
    def __init__(self):
        self.ids = ["device-A"]
        self.fail_clear = False
        self.developer_enabled = False
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(args)
        if args == ["usbmux", "list", "--simple"]:
            return json.dumps(self.ids)
        if args == ["lockdown", "info"]:
            return '{"DeviceName":"Test iPhone", "ProductVersion":"26.1"}'
        if args == ["amfi", "developer-mode-status"]:
            return json.dumps(self.developer_enabled)
        if args[-1] == "clear" and self.fail_clear:
            raise RuntimeError("Device disconnected")
        return ""


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.device = FakeDevice()
        self.bridge = server.Bridge(self.data, runner=self.device)

    def tearDown(self):
        self.bridge.stop_session()
        self.temp.cleanup()

    def configure_worker(self, code=None):
        self.bridge.save({"latitude": 39.5, "longitude": 116.5, "name": "Test"})
        (self.data / "device_worker.py").write_text(code or
            'import sys\nprint("LOCATION_BRIDGE_SET_ACK", flush=True)\nsys.stdin.readline()\n')

    def test_coordinates_reject_nonfinite_and_range(self):
        for lat, lon in [(float('nan'), 1), (1, float('inf')), (91, 1), (1, -181), (True, 1)]:
            with self.assertRaises(ValueError):
                server.valid_coordinates(lat, lon)

    def test_refuse_to_guess_target(self):
        with self.assertRaisesRegex(RuntimeError, "保存目标地点"):
            self.bridge.start()
        self.assertEqual(self.device.calls, [])

    def test_multiple_devices_not_silently_selected(self):
        self.device.ids = ["A", "B"]
        with self.assertRaisesRegex(RuntimeError, "多台"):
            self.bridge.check()

    def test_duplicate_usb_and_wifi_same_device(self):
        self.device.ids = ["device-A", "device-A"]
        self.bridge.check()
        self.assertEqual(self.bridge.udid, "device-A")

    def test_developer_button_reveals_toggle_not_direct_enable(self):
        self.bridge.developer()
        self.assertIn(["amfi", "reveal-developer-mode"], self.device.calls)
        self.assertNotIn(["amfi", "enable-developer-mode"], self.device.calls)
        self.assertIn("不需要关闭锁屏密码", self.bridge.message)

    def test_developer_already_enabled_does_not_change_device(self):
        self.device.developer_enabled = True
        self.bridge.developer()
        self.assertNotIn(["amfi", "reveal-developer-mode"], self.device.calls)
        self.assertNotIn(["amfi", "enable-developer-mode"], self.device.calls)
        self.assertIn("已开启", self.bridge.message)

    def test_set_ack_and_clear_real_subprocess(self):
        self.configure_worker()
        with patch.object(server, "ROOT", self.data):
            self.bridge.start()
            self.assertEqual(self.bridge.mode, "active")
            self.assertTrue((self.data / "session.json").exists())
            self.assertIsNone(self.bridge.proc.poll())
            self.bridge.clear()
        self.assertEqual(self.bridge.mode, "idle")
        self.assertFalse((self.data / "session.json").exists())

    def test_worker_failure_never_claims_success(self):
        self.configure_worker('raise SystemExit(1)\n')
        with patch.object(server, "ROOT", self.data):
            with self.assertRaises(RuntimeError):
                self.bridge.start()
        self.assertEqual(self.bridge.mode, "unknown")
        self.assertTrue((self.data / "session.json").exists())

    def test_failed_clear_preserves_unknown_and_original_phone(self):
        self.bridge.udid = "original"
        self.bridge.mode = "active"
        self.device.fail_clear = True
        with self.assertRaises(RuntimeError):
            self.bridge.clear()
        self.assertEqual(self.bridge.mode, "unknown")
        self.assertEqual(json.loads((self.data / "session.json").read_text())["udid"], "original")
        self.assertNotIn(["usbmux", "list", "--simple"], self.device.calls)

    def test_restart_retains_uncertain_state(self):
        server.write_json(self.data / "session.json", {"udid": "original"})
        other = server.Bridge(self.data, self.device)
        self.assertEqual(other.mode, "unknown")
        self.assertEqual(other.udid, "original")
        with self.assertRaises(RuntimeError):
            other.start()

    def test_no_device_switch_during_session(self):
        self.bridge.udid = "original"
        self.bridge.mode = "unknown"
        with self.assertRaisesRegex(RuntimeError, "原来的"):
            self.bridge.discover()
        self.assertEqual(self.bridge.udid, "original")

    def test_save_invalid_does_not_replace_valid(self):
        self.configure_worker()
        before = self.bridge.config.copy()
        with self.assertRaises(ValueError):
            self.bridge.save({"latitude": 999, "longitude": 1})
        self.assertEqual(before, self.bridge.config)

    def test_busy_rejects_competing_mutation(self):
        self.bridge.lock.acquire()
        try:
            with self.assertRaises(RuntimeError):
                self.bridge.submit("clear", {})
        finally:
            self.bridge.lock.release()

    def test_manual_session_survives_elapsed_day(self):
        self.configure_worker()
        with patch.object(server, "ROOT", self.data):
            self.bridge.start()
            with patch.object(server.time, "time", return_value=time.time()+86400):
                snapshot = self.bridge.snapshot()
            self.assertEqual(snapshot["mode"], "active")
            self.assertGreaterEqual(snapshot["elapsed"], 86400)
            self.assertNotIn("remaining", snapshot)
            self.assertIsNone(self.bridge.proc.poll())
            self.bridge.clear()

    def test_old_place_migrates_once_without_loss(self):
        old = {"name": "My original place", "latitude": -33.9, "longitude": 151.2}
        server.write_json(self.data / "place.json", old)
        migrated = server.Bridge(self.data, self.device)
        self.assertEqual(len(migrated.places), 1)
        self.assertEqual(migrated.config["name"], old["name"])
        self.assertEqual(migrated.config["latitude"], -33.9)
        again = server.Bridge(self.data, self.device)
        self.assertEqual(migrated.config, again.config)
        self.assertTrue((self.data / "place.json").exists())

    def test_multiple_places_edit_delete_persist(self):
        self.bridge.save({"name": "A", "latitude": 1, "longitude": 2})
        first = self.bridge.config["id"]
        self.bridge.save({"name": "B", "latitude": 3, "longitude": 4})
        second = self.bridge.config["id"]
        self.bridge.save({"id": first, "name": "A updated", "latitude": -1, "longitude": -2})
        self.assertEqual(len(self.bridge.places), 2)
        self.assertEqual(self.bridge.find_place(first)["name"], "A updated")
        self.bridge.delete({"id": first})
        reloaded = server.Bridge(self.data, self.device)
        self.assertEqual(reloaded.selected_id, second)
        self.assertEqual(len(reloaded.places), 1)
        self.bridge.delete({"id": second})
        self.assertEqual(self.bridge.places, [])
        self.assertIsNone(self.bridge.config["latitude"])

    def test_failed_save_leaves_memory_and_disk_unchanged(self):
        self.configure_worker()
        before = self.bridge.snapshot()["places"]
        with patch.object(server, "write_json", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.bridge.save({"name": "B", "latitude": 1, "longitude": 2})
        self.assertEqual(before, self.bridge.places)

    def test_live_switch_clears_original_before_new_worker(self):
        self.configure_worker()
        first = self.bridge.config["id"]
        self.bridge.save({"name": "B", "latitude": -10, "longitude": -20})
        second = self.bridge.config["id"]
        with patch.object(server, "ROOT", self.data):
            self.bridge.start(first)
            original = self.bridge.proc
            self.bridge.start(second)
            self.assertIsNotNone(original.poll())
            self.assertEqual(self.bridge.active_place["id"], second)
            self.assertIn(["developer", "dvt", "simulate-location", "clear"], self.device.calls)
            self.assertEqual(self.bridge.mode, "active")
            self.assertNotEqual(original.pid, self.bridge.proc.pid)
            self.bridge.clear()

    def test_reclick_current_place_keeps_session(self):
        self.configure_worker()
        with patch.object(server, "ROOT", self.data):
            self.bridge.start()
            original = self.bridge.proc
            self.bridge.start(self.bridge.active_place["id"])
            self.assertIs(self.bridge.proc, original)
            self.bridge.clear()

    def test_edit_active_is_saved_but_applied_only_on_click(self):
        self.configure_worker()
        with patch.object(server, "ROOT", self.data):
            self.bridge.start()
            current = self.bridge.active_place.copy()
            with self.assertRaises(RuntimeError):
                self.bridge.delete({"id": current["id"]})
            updated = {**current, "latitude": 30}
            self.bridge.save(updated)
            self.assertEqual(self.bridge.active_place, current)
            self.bridge.start(current["id"])
            self.assertEqual(self.bridge.active_place["latitude"], 30)
            self.bridge.clear()

    def test_failed_clear_prevents_switch_to_new_target(self):
        self.configure_worker()
        first = self.bridge.config["id"]
        self.bridge.save({"name": "B", "latitude": 10, "longitude": 20})
        second = self.bridge.config["id"]
        with patch.object(server, "ROOT", self.data):
            self.bridge.start(first)
            self.device.fail_clear = True
            with self.assertRaises(RuntimeError):
                self.bridge.start(second)
        self.assertEqual(self.bridge.mode, "unknown")
        self.assertEqual(self.bridge.selected_id, first)
        self.assertIsNone(self.bridge.proc)

    def test_exit_restores_and_close_is_idempotent(self):
        self.configure_worker()
        with patch.object(server, "ROOT", self.data):
            self.bridge.start()
            worker = self.bridge.proc
            self.bridge.close()
            self.bridge.close()
        self.assertIsNotNone(worker.poll())
        self.assertEqual(self.bridge.mode, "idle")
        self.assertFalse((self.data / "session.json").exists())
        self.assertEqual(sum(c[-1] == "clear" for c in self.device.calls), 1)

    def test_quit_failure_keeps_retry_available(self):
        self.bridge.udid = "device-A"
        self.bridge.mode = "unknown"
        self.device.fail_clear = True
        with self.assertRaises(RuntimeError):
            self.bridge.request_shutdown()
        self.assertFalse(self.bridge.closed)
        self.assertEqual(self.bridge.mode, "unknown")

    def test_quit_closes_and_rejects_new_actions(self):
        self.bridge.request_shutdown()
        self.assertTrue(self.bridge.closed)
        with self.assertRaisesRegex(RuntimeError, "退出"):
            self.bridge.submit("save", {})


class WorkerLifecycleTests(unittest.TestCase):
    """Exercise actual worker cleanup, with a fake installed device CLI."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        package = self.data / "pymobiledevice3"
        (package / "osu").mkdir(parents=True)
        (package / "__init__.py").touch()
        (package / "osu/__init__.py").touch()
        (package / "osu/os_utils.py").write_text(
            'class Utils: pass\nutils=Utils()\ndef get_os_utils(): return utils\n')
        (package / "__main__.py").write_text('''
import json, os, sys
from pathlib import Path
from pymobiledevice3.osu.os_utils import get_os_utils
folder=Path(os.environ['LOCATION_BRIDGE_DATA'])
args=sys.argv[1:]
if args == ['usbmux','list','--simple']:
    print('["fake-phone"]')
elif args == ['lockdown','info']:
    print('{"DeviceName":"Fake phone","ProductVersion":"26.1"}')
elif 'set' in args:
    (folder/'set.json').write_text(json.dumps(args))
    get_os_utils().wait_return()
elif args[-1] == 'clear':
    (folder/'clear.txt').write_text('clear requested')
    if os.environ.get('FAIL_CLEAR'): raise SystemExit(1)
''')
        self.env = {**os.environ, "PYTHONPATH": str(self.data),
                    "LOCATION_BRIDGE_DATA": str(self.data), "PYMOBILEDEVICE3_UDID": "fake-phone"}

    def tearDown(self):
        self.temp.cleanup()

    def worker(self, env=None):
        return subprocess.Popen([sys.executable, str(server.ROOT / "device_worker.py"), "-10", "-20"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env or self.env, start_new_session=True)

    def test_parent_pipe_eof_restores(self):
        proc = self.worker()
        try:
            self.assertEqual(proc.stdout.readline().strip(), server.ACK)
            out, err = proc.communicate(timeout=8)
            self.assertIn(server.CLEAR_ACK, out)
            self.assertTrue((self.data / "clear.txt").exists())
            self.assertEqual(proc.returncode, 0, err)
        finally:
            server.terminate(proc)

    def test_worker_termination_restores(self):
        proc = self.worker()
        try:
            self.assertEqual(proc.stdout.readline().strip(), server.ACK)
            proc.terminate()
            out, err = proc.communicate(timeout=8)
            self.assertIn(server.CLEAR_ACK, out)
            self.assertTrue((self.data / "clear.txt").exists())
        finally:
            server.terminate(proc)

    def test_worker_clear_failure_does_not_ack(self):
        proc = self.worker({**self.env, "FAIL_CLEAR": "1"})
        try:
            self.assertEqual(proc.stdout.readline().strip(), server.ACK)
            out, err = proc.communicate(timeout=8)
            self.assertNotIn(server.CLEAR_ACK, out)
            self.assertIn("自动恢复未完成", out)
        finally:
            server.terminate(proc)

    def test_orphan_cleanup_holds_session_lock(self):
        proc = self.worker()
        try:
            self.assertEqual(proc.stdout.readline().strip(), server.ACK)
            with self.assertRaises(RuntimeError):
                with server.session_lock(self.data, timeout=0.1):
                    pass
            proc.communicate(timeout=8)
            with server.session_lock(self.data, timeout=0.1):
                pass
        finally:
            server.terminate(proc)

    def test_worker_ack_avoids_duplicate_clear_on_normal_stop(self):
        bridge = server.Bridge(self.data, FakeDevice())
        bridge.save({"name": "Test", "latitude": 1, "longitude": 2})
        try:
            with patch.dict(os.environ, self.env):
                bridge.start()
                bridge.clear()
            self.assertTrue((self.data / "clear.txt").exists())
            self.assertNotIn(["developer", "dvt", "simulate-location", "clear"], bridge.runner.calls)
        finally:
            bridge.stop_session()

    def test_server_exit_signals_and_crash_restore(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGKILL):
            with self.subTest(signal=sig):
                (self.data / "clear.txt").unlink(missing_ok=True)
                (self.data / "session.json").unlink(missing_ok=True)
                server.write_json(self.data / "access-token", "unused")
                (self.data / "access-token").write_text("test-token")
                server.write_json(self.data / "places.json", {"places": [
                    {"id": "a", "name": "A", "latitude": 1, "longitude": 2}], "selected_id": "a"})
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                with (self.data / "server.log").open("w") as log:
                    proc = subprocess.Popen([sys.executable, "-u", str(server.ROOT / "server.py"),
                                             "--host", "127.0.0.1", "--port", str(port),
                                             "--data-dir", str(self.data), "--no-browser"],
                                            env=self.env, stdout=log, stderr=log, start_new_session=True)
                    def request(method, body=None):
                        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                        try:
                            connection.request(method, "/api/action" if body else "/api/status",
                                               body=body, headers={"Authorization": "Bearer test-token",
                                                                  "Content-Type": "application/json"})
                            response = connection.getresponse()
                            return response.status, json.loads(response.read())
                        finally:
                            connection.close()
                    try:
                        deadline = time.monotonic()+8
                        while True:
                            try:
                                request("GET")
                                break
                            except OSError:
                                if time.monotonic() > deadline: self.fail("Server did not start")
                                time.sleep(.05)
                        self.assertEqual(request("POST", '{"action":"start","id":"a"}')[0], 202)
                        while request("GET")[1]["mode"] != "active":
                            if time.monotonic() > deadline: self.fail("Session did not start")
                            time.sleep(.05)
                        proc.send_signal(sig)
                        proc.wait(timeout=8)
                        deadline = time.monotonic()+8
                        while not (self.data / "clear.txt").exists():
                            if time.monotonic() > deadline: self.fail("Exit did not request restore")
                            time.sleep(.05)
                        with server.session_lock(self.data, timeout=5):
                            pass
                        self.assertEqual((self.data / "session.json").exists(), sig == signal.SIGKILL)
                    finally:
                        server.terminate(proc)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.bridge = server.Bridge(cls.temp.name, FakeDevice())
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(cls.bridge, "secret", "127.0.0.1:8765", "http://127.0.0.1:8765/#token=secret"))
        cls.port = cls.httpd.server_port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, method, path, data=None, **headers):
        base={"Host":"127.0.0.1:8765", **headers}
        conn=http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method,path,body=data,headers=base)
        response=conn.getresponse()
        result=(response.status,response.read())
        conn.close()
        return result

    def test_status_requires_token(self):
        self.assertEqual(self.request("GET","/api/status")[0],401)

    def test_qr_requires_token(self):
        self.assertEqual(self.request("GET","/api/pairing.svg")[0],401)

    def test_authorized_status(self):
        status,body=self.request("GET","/api/status",Authorization="Bearer secret")
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)["mode"],"idle")

    def test_dns_rebinding_host_denied(self):
        self.assertEqual(self.request("GET","/",Host="evil.example")[0],403)

    def test_cross_origin_denied(self):
        self.assertEqual(self.request("POST","/api/action",'{}',Authorization="Bearer secret",Origin="http://evil.example",**{"Content-Type":"application/json"})[0],403)

    def test_plain_form_denied(self):
        self.assertEqual(self.request("POST","/api/action","action=start",Authorization="Bearer secret",**{"Content-Type":"application/x-www-form-urlencoded"})[0],415)

    def test_no_path_traversal(self):
        self.assertEqual(self.request("GET","/../server.py")[0],404)

    def test_unknown_command_denied(self):
        self.assertEqual(self.request("POST","/api/action",'{"action":"shell"}',Authorization="Bearer secret",**{"Content-Type":"application/json"})[0],400)

    def test_missing_auth_cannot_start(self):
        self.assertEqual(self.request("POST","/api/action",'{"action":"start"}',**{"Content-Type":"application/json"})[0],401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
