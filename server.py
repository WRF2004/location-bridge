#!/usr/bin/env python3
"""Local-only phone control panel. Python stdlib HTTP server, no cloud account."""
import argparse
import atexit
import collections
import ipaddress
import json
import math
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import webbrowser
from session_guard import session_lock

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = Path.home() / "Library/Application Support/LocationBridge"
ACK = "LOCATION_BRIDGE_SET_ACK"
CLEAR_ACK = "LOCATION_BRIDGE_CLEAR_ACK"


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def valid_coordinates(lat, lon):
    if isinstance(lat, bool) or isinstance(lon, bool):
        raise ValueError("请输入数字经纬度")
    lat, lon = float(lat), float(lon)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("经纬度超出范围")
    return lat, lon


def terminate(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=4)


class Bridge:
    def __init__(self, data_dir, runner=None):
        self.data = Path(data_dir)
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data.chmod(0o700)
        self.places = []
        self.selected_id = None
        places_file = self.data / "places.json"
        if places_file.exists():
            saved = json.loads(places_file.read_text())
            self.places = saved["places"]
            self.selected_id = saved.get("selected_id")
        elif (self.data / "place.json").exists():
            old = json.loads((self.data / "place.json").read_text())
            if old.get("latitude") is not None and old.get("longitude") is not None:
                lat, lon = valid_coordinates(old["latitude"], old["longitude"])
                self.selected_id = secrets.token_hex(8)
                self.places = [{"id": self.selected_id, "name": old.get("name", "保存的地点"),
                                "latitude": lat, "longitude": lon}]
                self.persist_places()
        self.active_place = None
        self.udid = None
        self.device_name = "尚未检查连接"
        self.mode = "idle"
        self.message = "首次使用：连接 iPhone，点击检查连接。"
        self.error = None
        self.started_at = None
        self.restored = threading.Event()
        self.proc = None
        self.child = None
        self.logs = collections.deque(maxlen=50)
        self.lock = threading.Lock()
        self.busy = False
        self.closed = False
        self.close_lock = threading.Lock()
        self.shutdown_callback = None
        self.runner = runner
        journal = self.data / "session.json"
        if journal.exists():
            self.udid = json.loads(journal.read_text()).get("udid")
            self.mode = "unknown"
            self.message = "上次会话未确认恢复。请连接原来的 iPhone，点击恢复真实定位。"

    @property
    def config(self):
        return next((p for p in self.places if p["id"] == self.selected_id),
                    self.places[0] if self.places else
                    {"id": None, "name": "尚未保存地点", "latitude": None, "longitude": None})

    def persist_places(self):
        write_json(self.data / "places.json", {"places": self.places, "selected_id": self.selected_id})

    def find_place(self, place_id):
        for place in self.places:
            if place["id"] == place_id:
                return place
        raise ValueError("地点不存在，请刷新页面后重试。")

    def log(self, line):
        # Device identifiers are kept out of browser logs where possible.
        line = str(line).strip()
        if self.udid:
            line = line.replace(self.udid, "[设备]")
        if line:
            self.logs.append(line[-600:])

    def env(self):
        env = os.environ.copy()
        env.update(PYTHONUNBUFFERED="1", NO_COLOR="1", TERM="dumb")
        env["LOCATION_BRIDGE_DATA"] = str(self.data)
        if self.udid:
            env["PYMOBILEDEVICE3_UDID"] = self.udid
        else:
            env.pop("PYMOBILEDEVICE3_UDID", None)
        return env

    def command(self, args, timeout=45):
        if self.runner:
            return self.runner(args, timeout)
        proc = subprocess.Popen([sys.executable, "-m", "pymobiledevice3", *args],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=self.env())
        self.child = proc
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate(proc)
            raise RuntimeError("连接超时。请解锁手机、检查数据线或同一局域网，再重试。")
        finally:
            self.child = None
        for line in err.splitlines()[-8:]:
            self.log(line)
        if proc.returncode:
            raise RuntimeError((err or out or "设备命令失败")[-1200:])
        return out

    def discover(self):
        # --simple works even before trust pairing; no implicit device choice.
        devices = list(dict.fromkeys(json.loads(self.command(["usbmux", "list", "--simple"]))))
        if self.udid and self.mode in ("active", "unknown", "starting"):
            if self.udid not in devices:
                raise RuntimeError("原来的 iPhone 未连接。请连接原设备；不会改动其他手机。")
        elif len(devices) == 1:
            self.udid = devices[0]
        elif not devices:
            raise RuntimeError("未发现 iPhone。首次请用 USB 连接并解锁手机。")
        else:
            raise RuntimeError("发现多台设备。请只保留要操作的 iPhone，然后重试。")
        info = json.loads(self.command(["lockdown", "info"]))
        self.device_name = f"{info.get('DeviceName', 'iPhone')} · iOS {info.get('ProductVersion', '?')}"
        return info

    def check(self):
        self.discover()
        self.message = "已连接。首次使用请完成下方设置；已配置则可直接设置位置。"

    def developer(self):
        self.discover()
        status = json.loads(self.command(["amfi", "developer-mode-status"]))
        if status is True:
            self.message = "开发者模式已开启，可以点击“准备设备”。"
            return
        # Only reveal the Settings toggle. Direct enable is rejected on passcode-
        # protected devices and is not a substitute for confirmation on the phone.
        self.command(["amfi", "reveal-developer-mode"], timeout=45)
        self.message = ("已请求显示开发者模式开关。请退出并重新打开 iPhone 设置 → 隐私与安全性，"
                        "滑到底部开启“开发者模式”，按手机提示重启并确认；不需要关闭锁屏密码。"
                        "完成后回来点击“准备设备”。")

    def prepare(self):
        self.discover()
        self.message = "正在准备设备，首次会下载开发者映像，请稍候…"
        self.command(["mounter", "auto-mount"], timeout=240)
        self.message = "设备准备命令已完成。保存地点后可设置位置。"

    def wifi(self):
        self.discover()
        self.command(["lockdown", "wifi-connections", "on"])
        self.message = "已开启无线连接。拔线后点检查连接验证；失败请继续用 USB。"

    def save(self, payload):
        lat, lon = valid_coordinates(payload.get("latitude"), payload.get("longitude"))
        name = str(payload.get("name", "保存的地点")).strip()[:80] or "保存的地点"
        place_id = payload.get("id")
        if place_id:
            self.find_place(place_id)
        elif len(self.places) >= 100:
            raise ValueError("最多保存 100 个地点，请先删除不需要的地点。")
        else:
            place_id = secrets.token_hex(8)
        place = {"id": place_id, "name": name, "latitude": lat, "longitude": lon}
        updated = [place if p["id"] == place_id else p for p in self.places]
        if not any(p["id"] == place_id for p in self.places):
            updated.append(place)
        selected = place_id if self.mode == "idle" else self.selected_id
        write_json(self.data / "places.json", {"places": updated, "selected_id": selected})
        self.places, self.selected_id = updated, selected
        self.message = "地点已保存。点击地点卡片即可开启或切换定位。"
        if self.mode == "active":
            self.message += "当前定位保持不变，点击卡片后才应用修改。"

    def delete(self, payload):
        place = self.find_place(payload.get("id"))
        if self.active_place and self.active_place["id"] == place["id"]:
            raise RuntimeError("请先恢复真实定位或切换到其他地点，再删除当前地点。")
        updated = [p for p in self.places if p["id"] != place["id"]]
        selected = self.selected_id
        if selected == place["id"]:
            selected = updated[0]["id"] if updated else None
        write_json(self.data / "places.json", {"places": updated, "selected_id": selected})
        self.places, self.selected_id = updated, selected
        self.message = "地点已删除。"

    def stop_session(self):
        proc, self.proc = self.proc, None
        if proc:
            if proc.poll() is None:
                try:
                    proc.stdin.write("\n")
                    proc.stdin.flush()
                    # Worker restores before exiting, including after parent EOF.
                    proc.wait(timeout=65)
                except (OSError, subprocess.TimeoutExpired):
                    terminate(proc)
            if proc.stdin:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            reader = getattr(proc, "bridge_reader", None)
            if reader:
                reader.join(timeout=2)

    def clear(self):
        # Preserve the original device identity even when disconnected.
        if not self.udid:
            self.discover()
        self.stop_session()
        self.started_at = None
        self.mode = "unknown"
        write_json(self.data / "session.json", {"udid": self.udid})
        if not self.restored.is_set():
            # Also waits for cleanup by a worker orphaned by an earlier server.
            with session_lock(self.data):
                self.command(["developer", "dvt", "simulate-location", "clear"], timeout=60)
        self.restored.clear()
        self.mode = "idle"
        self.active_place = None
        (self.data / "session.json").unlink(missing_ok=True)
        self.message = "恢复命令已完成。请打开手机地图确认位置已恢复。"

    def start(self, place_id=None):
        if self.closed:
            raise RuntimeError("程序正在退出。")
        if self.mode == "starting":
            raise RuntimeError("正在设置位置，请稍候。")
        if self.mode == "unknown":
            raise RuntimeError("上次定位状态未确认，请先恢复真实定位。")
        target = self.find_place(place_id) if place_id else self.config
        if target["latitude"] is None or target["longitude"] is None:
            raise RuntimeError("请先添加并保存目标地点。")
        target = target.copy()
        lat, lon = valid_coordinates(target["latitude"], target["longitude"])
        if self.mode == "active":
            if self.active_place == target and self.proc and self.proc.poll() is None:
                self.message = "当前已使用这个地点；保持开启，直到你手动恢复或退出程序。"
                return
            self.message = "正在切换地点…"
            self.clear()
        self.selected_id = target["id"]
        self.persist_places()
        self.discover()
        write_json(self.data / "session.json", {"udid": self.udid})
        self.mode = "starting"
        self.message = "正在建立定位会话…"
        self.restored.clear()
        proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "device_worker.py"), str(lat), str(lon)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1, env=self.env(),
                                start_new_session=True)
        self.proc = proc
        ack = threading.Event()

        def read_output():
            try:
                for line in proc.stdout:
                    if line.strip() == ACK:
                        ack.set()
                    elif line.strip() == CLEAR_ACK:
                        self.restored.set()
                    else:
                        self.log(line)
            finally:
                proc.stdout.close()

        proc.bridge_reader = threading.Thread(target=read_output, daemon=True)
        proc.bridge_reader.start()
        deadline = time.monotonic() + 90
        while not ack.wait(0.1):
            if proc.poll() is not None or time.monotonic() > deadline or self.closed:
                self.stop_session()
                self.mode = "unknown"
                raise RuntimeError("未收到定位设置确认。请查看诊断信息；建议先恢复，再重试。")
        if proc.poll() is not None or self.closed:
            self.stop_session()
            self.mode = "unknown"
            raise RuntimeError("定位会话意外退出，请恢复后重试。")
        self.mode = "active"
        self.active_place = target
        self.started_at = time.time()
        self.message = "模拟定位已开启，不设时间限制。请在手机地图核对；用完手动关闭。"

    def snapshot(self):
        if not self.busy and self.mode == "active" and self.proc and self.proc.poll() is not None:
            self.mode = "unknown"
            self.started_at = None
            self.message = "定位会话已断开，实际定位状态未知。请重新连接并恢复真实定位。"
        return {"mode": self.mode, "busy": self.busy, "message": self.message, "error": self.error,
                "device": self.device_name, "place": self.config.copy(),
                "places": [p.copy() for p in self.places], "selected_id": self.selected_id,
                "active_place": self.active_place.copy() if self.active_place else None,
                "elapsed": max(0, int(time.time() - self.started_at)) if self.started_at else None,
                "logs": list(self.logs)}

    def submit(self, action, payload):
        handlers = {"check": self.check, "developer": self.developer, "prepare": self.prepare,
                    "wifi": self.wifi, "start": lambda: self.start(payload.get("id")),
                    "clear": self.clear, "shutdown": self.request_shutdown,
                    "save": lambda: self.save(payload), "delete": lambda: self.delete(payload)}
        if action not in handlers:
            raise ValueError("未知操作")
        if self.closed:
            raise RuntimeError("程序正在退出。")
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("上一步还在执行，请稍候。")
        self.busy = True
        self.error = None
        self.message = "正在处理，请稍候…"

        def task():
            try:
                if action in ("developer", "prepare", "wifi") and self.mode != "idle":
                    raise RuntimeError("请先恢复真实定位，再修改设备设置。")
                handlers[action]()
            except Exception as exc:
                self.message = str(exc) or type(exc).__name__
                self.error = self.message
                self.log(self.message)
                if self.mode == "starting":
                    self.mode = "unknown"
            finally:
                self.busy = False
                self.lock.release()

        threading.Thread(target=task, daemon=True).start()

    def request_shutdown(self):
        if self.mode != "idle":
            self.message = "正在恢复真实定位，完成后退出…"
            self.clear()
        # A failed clear leaves the UI running, with its retry button available.
        self.closed = True
        self.message = "恢复流程已完成，程序正在退出。"
        if self.shutdown_callback:
            threading.Thread(target=self.shutdown_callback, daemon=True).start()

    def close(self):
        if not self.close_lock.acquire(blocking=False):
            return
        self.closed = True
        terminate(self.child)
        if self.lock.acquire(timeout=8):
            try:
                if self.mode in ("active", "starting", "unknown"):
                    try:
                        self.clear()
                    except Exception:
                        print("未能确认恢复真实定位。请重新启动并点击恢复，或重启 iPhone 后检查地图。", flush=True)
            finally:
                self.lock.release()
        else:
            self.stop_session()


def handler_for(bridge, token, authority, url):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, payload, content_type="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def validate(self, auth=False):
            if self.headers.get("Host") != authority:
                self.reply(403, {"error": "Host 不匹配，请使用 Mac 上显示的地址"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + authority:
                self.reply(403, {"error": "不允许跨站请求"})
                return False
            if auth and not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.reply(401, {"error": "连接码无效。请重新扫描 Mac 上的二维码。"})
                return False
            return True

        def do_GET(self):
            path = urlsplit(self.path).path
            protected = path in ("/api/status", "/api/pairing.svg", "/api/link")
            if not self.validate(protected):
                return
            if path == "/api/status":
                self.reply(200, bridge.snapshot())
            elif path == "/api/link":
                self.reply(200, {"url": url})
            elif path == "/api/pairing.svg":
                import io
                import segno
                output = io.BytesIO()
                segno.make(url, micro=False).save(output, kind="svg", scale=5)
                self.reply(200, output.getvalue(), "image/svg+xml")
            elif path in ("/", "/app.js", "/style.css"):
                file, mime = {"/": ("index.html", "text/html; charset=utf-8"),
                              "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                              "/style.css": ("style.css", "text/css; charset=utf-8")}[path]
                self.reply(200, (ROOT / file).read_bytes(), mime)
            else:
                self.reply(404, {"error": "页面不存在"})

        def do_POST(self):
            if not self.validate(True):
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.reply(415, {"error": "需要 JSON"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("请求大小无效")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("请求格式无效")
                path = urlsplit(self.path).path
                if path != "/api/action":
                    self.reply(404, {"error": "接口不存在"})
                    return
                bridge.submit(payload.get("action"), payload)
                self.reply(202, {"accepted": True})
            except (ValueError, TypeError) as exc:
                self.reply(400, {"error": str(exc)})
            except RuntimeError as exc:
                self.reply(409, {"error": str(exc)})

    return Handler


def local_ip():
    # Mac: select a real private LAN interface, not a VPN/public address.
    if sys.platform == "darwin":
        for interface in ("en0", "en1", "en2", "en3", "bridge100"):
            result = subprocess.run(["/usr/sbin/ipconfig", "getifaddr", interface], capture_output=True, text=True)
            candidate = result.stdout.strip()
            try:
                ip = ipaddress.ip_address(candidate)
                if ip.is_private and not ip.is_loopback and not ip.is_link_local:
                    return candidate
            except ValueError:
                pass
    return "127.0.0.1"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    host = args.host or local_ip()
    ip = ipaddress.ip_address(host)
    if not ip.is_private or ip.is_unspecified:
        parser.error("仅允许绑定明确的本地或私有 IPv4 地址")
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.data_dir.chmod(0o700)
    # One process per data directory prevents competing sessions, even on another port.
    import fcntl
    instance_lock = (args.data_dir / "server.lock").open("w")
    try:
        fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("工具已经在运行，请使用原来的页面。")
        return 1
    token_path = args.data_dir / "access-token"
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(32))
        token_path.chmod(0o600)
    token = token_path.read_text().strip()
    authority = f"{host}:{args.port}"
    url = f"http://{authority}/#token={token}"
    bridge = Bridge(args.data_dir)
    server = ThreadingHTTPServer((host, args.port), handler_for(bridge, token, authority, url))
    server.daemon_threads = True
    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    bridge.shutdown_callback = server.shutdown
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGHUP, shutdown)
    atexit.register(bridge.close)
    print("\nLocation Bridge 已启动。Mac 与手机请使用同一可信局域网。")
    print("浏览器内显示手机二维码；保留此窗口，关闭窗口会尝试恢复定位。")
    print(url, flush=True)
    if host == "127.0.0.1":
        print("未找到局域网地址，目前只能在 Mac 本机打开。请连接 Wi-Fi 后重新启动。")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
