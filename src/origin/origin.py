"""源站 Origin：提供对象 + 正确的缓存语义（ETag / Last-Modified / max-age / 条件 GET）。

运行： python src/origin/origin.py [--port 9000] [--max-age 5]
观测： GET /origin-stats   被回源次数、304 次数、输出字节
控制： GET /admin/max-age?seconds=N   动态改下发的 max-age（实验 E3 用）
"""
import argparse
import hashlib
import json
import mimetypes
import os
import socket
import socketserver
import sys
import threading
import time
from email.utils import formatdate, parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common import config, console  # noqa: E402
from common.http_util import build_response, read_request  # noqa: E402

console.setup()

STATE = {
    "requests": 0,
    "not_modified": 0,
    "not_found": 0,
    "bytes_served": 0,
    "range_requests": 0,
    "max_age": config.ORIGIN["max_age"],
    "delay_ms": config.ORIGIN["delay_ms"],
}
LOCK = threading.Lock()
_ETAG_CACHE = {}  # (path, mtime_ns, size) -> etag


def etag_for(full_path, stat):
    key = (full_path, stat.st_mtime_ns, stat.st_size)
    etag = _ETAG_CACHE.get(key)
    if etag:
        return etag
    digest = hashlib.md5()
    with open(full_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    etag = '"%s"' % digest.hexdigest()[:16]
    _ETAG_CACHE[key] = etag
    return etag


def parse_range(header, total):
    """仅支持单段 bytes=start-end。返回 (start, end) 或 None。"""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):].split(",")[0].strip()
    start_text, _, end_text = spec.partition("-")
    try:
        if start_text == "":
            length = int(end_text)
            return max(0, total - length), total - 1
        start = int(start_text)
        end = int(end_text) if end_text else total - 1
    except ValueError:
        return None
    if start >= total:
        return None
    return start, min(end, total - 1)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        conn = self.request
        conn.settimeout(15)
        while True:
            try:
                request = read_request(conn)
            except socket.timeout:
                return
            except (ConnectionResetError, OSError):
                return
            except ValueError:
                self.reply(400, {}, b"", "GET", False)
                return

            if request is None:
                return
            method, target, _version, headers, _body = request
            keep_alive = (headers.get("connection", "").lower() != "close")
            status, out_headers, payload = self.route(method, target, headers)
            try:
                conn.sendall(build_response(status, out_headers, payload, method, keep_alive))
            except OSError:
                return
            if not keep_alive:
                return

    def reply(self, status, headers, payload, method, keep_alive=True):
        try:
            self.request.sendall(build_response(status, headers, payload, method, keep_alive))
        except OSError:
            pass

    # ------------------------------------------------------------------ 路由
    def route(self, method, target, headers):
        path, _, _query = target.partition("?")

        if path == "/health":
            return 200, {"Content-Type": "text/plain"}, b"ok"
        if path == "/origin-stats":
            with LOCK:
                snapshot = dict(STATE)
            snapshot["objects"] = len(os.listdir(config.DATA_DIR))
            return 200, {"Content-Type": "application/json"}, json.dumps(snapshot).encode()
        if path == "/admin/max-age":
            seconds = int(dict(p.split("=") for p in _query.split("&") if "=" in p).get("seconds", "5"))
            with LOCK:
                STATE["max_age"] = max(0, seconds)
            print("[origin] max-age -> %ds" % seconds, flush=True)
            return 200, {"Content-Type": "text/plain"}, b"ok"
        if path == "/admin/delay":
            ms = int(dict(p.split("=") for p in _query.split("&") if "=" in p).get("ms", "0"))
            with LOCK:
                STATE["delay_ms"] = max(0, ms)
            print("[origin] delay -> %d ms" % ms, flush=True)
            return 200, {"Content-Type": "text/plain"}, b"ok"
        if method not in ("GET", "HEAD"):
            return 405, {"Allow": "GET, HEAD"}, b"method not allowed"

        name = path.lstrip("/")
        full = os.path.abspath(os.path.join(config.DATA_DIR, name))
        if not name or not full.startswith(os.path.abspath(config.DATA_DIR)) or not os.path.isfile(full):
            with LOCK:
                STATE["not_found"] += 1
            print("[origin] 404 %s" % path, flush=True)
            return 404, {"Content-Type": "text/plain"}, b"not found"

        stat = os.stat(full)
        etag = etag_for(full, stat)
        with open(full, "rb") as fh:
            data = fh.read()

        out = {
            "Content-Type": mimetypes.guess_type(full)[0] or "application/octet-stream",
            "ETag": etag,
            "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
            "Cache-Control": "max-age=%d" % STATE["max_age"],
            "Accept-Ranges": "bytes",
            "X-Origin-Object": name,
        }

        with LOCK:
            STATE["requests"] += 1
            delay_ms = STATE["delay_ms"]
        if delay_ms:
            time.sleep(delay_ms / 1000.0)   # 模拟源站与用户之间的链路时延

        # 条件请求：If-None-Match 优先，其次 If-Modified-Since
        inm = (headers.get("if-none-match") or "").strip()
        ims = (headers.get("if-modified-since") or "").strip()
        if inm and inm == etag:
            with LOCK:
                STATE["not_modified"] += 1
            print("[origin] 304 %s (If-None-Match)" % path, flush=True)
            return 304, out, b""
        if ims and not inm:
            try:
                if parsedate_to_datetime(ims).timestamp() >= int(stat.st_mtime):
                    with LOCK:
                        STATE["not_modified"] += 1
                    print("[origin] 304 %s (If-Modified-Since)" % path, flush=True)
                    return 304, out, b""
            except (TypeError, ValueError):
                pass

        rng = parse_range(headers.get("range"), len(data))
        if rng:
            start, end = rng
            chunk = data[start:end + 1]
            out["Content-Range"] = "bytes %d-%d/%d" % (start, end, len(data))
            with LOCK:
                STATE["range_requests"] += 1
                STATE["bytes_served"] += len(chunk)
            print("[origin] 206 %s bytes=%d-%d" % (path, start, end), flush=True)
            return 206, out, chunk

        with LOCK:
            STATE["bytes_served"] += len(data)
        print("[origin] 200 %s (%d B)" % (path, len(data)), flush=True)
        return 200, out, data


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=config.ORIGIN["host"])
    parser.add_argument("--port", type=int, default=config.ORIGIN["port"])
    parser.add_argument("--max-age", type=int, default=config.ORIGIN["max_age"])
    parser.add_argument("--delay", type=int, default=config.ORIGIN["delay_ms"],
                        help="人为响应时延（毫秒），模拟源站远离用户")
    args = parser.parse_args()

    config.ensure_dirs()
    STATE["max_age"] = args.max_age
    STATE["delay_ms"] = args.delay
    if not os.path.isdir(config.DATA_DIR) or not os.listdir(config.DATA_DIR):
        print("[origin] 警告：%s 为空，请先运行 scripts/make_objects.py" % config.DATA_DIR)

    server = Server((args.host, args.port), Handler)
    print("[origin] listening http://%s:%d  (objects: %s, max-age=%ds, delay=%dms)"
          % (args.host, args.port, config.DATA_DIR, args.max_age, args.delay), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("[origin] stopped", flush=True)


if __name__ == "__main__":
    main()
