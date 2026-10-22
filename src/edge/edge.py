"""边缘缓存节点 Edge：HTTP 缓存代理（LRU + TTL + 条件请求再验证）。

运行： python src/edge/edge.py --id A
      python src/edge/edge.py --id B
行为：
  命中且新鲜      -> X-Cache: HIT
  未命中          -> 回源，X-Cache: MISS
  有副本但过期    -> 带 If-None-Match 回源；304 则 X-Cache: REVALIDATED
观测： GET /stats         命中率、回源字节、淘汰次数、在途请求数
控制： GET /admin/clear           清空缓存
      GET /admin/delay?ms=100    人为加延迟（模拟与客户端之间的链路时延）
      GET /admin/capacity?n=3    改缓存容量（触发 LRU 淘汰）
"""
import argparse
import json
import os
import socket
import socketserver
import sys
import threading
import time
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common import config, console, http_client  # noqa: E402
from common.http_util import (  # noqa: E402
    build_response, is_cacheable, parse_max_age, read_request,
)

console.setup()

EDGE_ID = "A"
DELAY_MS = 0
LOCK = threading.Lock()
STATS = {
    "requests": 0,
    "hits": 0,
    "misses": 0,
    "revalidated": 0,
    "evictions": 0,
    "bytes_from_origin": 0,
    "bytes_to_client": 0,
    "inflight": 0,
    "errors": 0,
}


class CacheEntry:
    __slots__ = ("body", "etag", "last_modified", "stored_at", "ttl", "content_type", "size")

    def __init__(self, body, etag, last_modified, ttl, content_type):
        self.body = body
        self.etag = etag
        self.last_modified = last_modified
        self.stored_at = time.time()
        self.ttl = ttl
        self.content_type = content_type
        self.size = len(body)

    def is_fresh(self, now=None):
        now = now or time.time()
        return (now - self.stored_at) < self.ttl

    def age(self, now=None):
        return int((now or time.time()) - self.stored_at)


class LRUCache:
    def __init__(self, capacity):
        self.capacity = max(1, capacity)
        self.data = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            entry = self.data.get(key)
            if entry is not None:
                self.data.move_to_end(key)
            return entry

    def put(self, key, entry):
        with self.lock:
            self.data[key] = entry
            self.data.move_to_end(key)
            while len(self.data) > self.capacity:
                _, _ = self.data.popitem(last=False)
                with LOCK:
                    STATS["evictions"] += 1

    def clear(self):
        with self.lock:
            count = len(self.data)
            self.data.clear()
            return count

    def keys(self):
        with self.lock:
            return list(self.data.keys())


CACHE = LRUCache(config.EDGES[0]["capacity"] if EDGE_ID == "A" else config.EDGES[1]["capacity"])


def log(message):
    print("[edge %s] %s" % (EDGE_ID, message), flush=True)


def edge_headers(cache_state, ttl=None):
    headers = {"X-Edge": EDGE_ID, "X-Cache": cache_state, "Via": "cdn-edge-%s" % EDGE_ID}
    return headers


def fetch_origin(path, method="GET", extra_headers=None):
    started = time.perf_counter()
    status, headers, body = http_client.request(
        config.ORIGIN["host"], config.ORIGIN["port"], path,
        method=method, headers=extra_headers, timeout=10.0,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    with LOCK:
        STATS["bytes_from_origin"] += len(body)
    return status, headers, body, elapsed_ms


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
                self.send(400, {}, b"", "GET", False)
                return

            if request is None:
                return
            method, target, _version, headers, _body = request
            keep_alive = (headers.get("connection", "").lower() != "close")

            with LOCK:
                STATS["inflight"] += 1
            try:
                status, out_headers, payload = self.route(method, target, headers)
            except (OSError, ConnectionError) as exc:
                with LOCK:
                    STATS["errors"] += 1
                log("回源失败 %s: %s" % (target, exc))
                status, out_headers, payload = 502, {"Content-Type": "text/plain"}, b"bad gateway"
            finally:
                with LOCK:
                    STATS["inflight"] -= 1

            try:
                conn.sendall(build_response(status, out_headers, payload, method, keep_alive))
            except OSError:
                return
            if not keep_alive:
                return

    def send(self, status, headers, payload, method, keep_alive=True):
        try:
            self.request.sendall(build_response(status, headers, payload, method, keep_alive))
        except OSError:
            pass

    # ------------------------------------------------------------------ 路由
    def route(self, method, target, headers):
        global DELAY_MS
        path, _, query = target.partition("?")

        if path == "/health":
            return 200, {"Content-Type": "text/plain"}, b"ok"
        if path == "/stats":
            with LOCK:
                snapshot = dict(STATS)
            snapshot.update({
                "edge": EDGE_ID,
                "delay_ms": DELAY_MS,
                "cache_entries": len(CACHE.keys()),
                "cache_capacity": CACHE.capacity,
                "hit_ratio": round(STATS["hits"] / STATS["requests"], 4) if STATS["requests"] else 0.0,
            })
            return 200, {"Content-Type": "application/json"}, json.dumps(snapshot).encode()
        if path == "/admin/clear":
            removed = CACHE.clear()
            log("缓存已清空（%d 项）" % removed)
            return 200, {"Content-Type": "text/plain"}, b"cleared %d" % removed
        if path == "/admin/reset-stats":
            with LOCK:
                for key in ("requests", "hits", "misses", "revalidated", "evictions",
                            "bytes_from_origin", "bytes_to_client", "errors"):
                    STATS[key] = 0
            log("统计已清零")
            return 200, {"Content-Type": "text/plain"}, b"ok"
        if path == "/admin/delay":
            params = dict(p.split("=") for p in query.split("&") if "=" in p)
            DELAY_MS = max(0, int(params.get("ms", "0")))
            log("链路时延设为 %d ms" % DELAY_MS)
            return 200, {"Content-Type": "text/plain"}, b"delay=%d" % DELAY_MS
        if path == "/admin/capacity":
            params = dict(p.split("=") for p in query.split("&") if "=" in p)
            CACHE.capacity = max(1, int(params.get("n", str(CACHE.capacity))))
            log("缓存容量设为 %d 项" % CACHE.capacity)
            return 200, {"Content-Type": "text/plain"}, b"capacity=%d" % CACHE.capacity
        if method not in ("GET", "HEAD"):
            return 405, {"Allow": "GET, HEAD"}, b"method not allowed"

        if DELAY_MS:
            time.sleep(DELAY_MS / 1000.0)

        with LOCK:
            STATS["requests"] += 1

        entry = CACHE.get(path)
        now = time.time()

        # 1) 新鲜命中
        if entry is not None and entry.is_fresh(now):
            with LOCK:
                STATS["hits"] += 1
            return self.serve_cached("HIT", entry, method)

        # 2) 有副本但过期 -> 条件请求再验证
        if entry is not None and entry.etag:
            extra = {"If-None-Match": entry.etag}
            if entry.last_modified:
                extra["If-Modified-Since"] = entry.last_modified
            status, headers, body, elapsed = fetch_origin(path, method, extra)
            if status == 304:
                entry.stored_at = time.time()
                entry.ttl = parse_max_age(headers.get("cache-control"), entry.ttl)
                with LOCK:
                    STATS["revalidated"] += 1
                log("REVALIDATED %s (304, %.1f ms)" % (path, elapsed))
                return self.serve_cached("REVALIDATED", entry, method)
            if status == 200:
                self.store(path, headers, body, method)
                with LOCK:
                    STATS["misses"] += 1
                log("MISS(重取) %s (%d B, %.1f ms)" % (path, len(body), elapsed))
                return 200, self.passthrough_headers(headers, "MISS"), body
            return status, headers, body

        # 3) 完全未命中
        status, headers, body, elapsed = fetch_origin(path, method)
        if status == 200:
            self.store(path, headers, body, method)
            with LOCK:
                STATS["misses"] += 1
            log("MISS %s (%d B, %.1f ms)" % (path, len(body), elapsed))
        return status, self.passthrough_headers(headers, "MISS"), body

    def serve_cached(self, cache_state, entry, method):
        headers = edge_headers(cache_state)
        headers["Content-Type"] = entry.content_type
        headers["Age"] = str(entry.age())
        if entry.etag:
            headers["ETag"] = entry.etag
        if entry.last_modified:
            headers["Last-Modified"] = entry.last_modified
        headers["Content-Length"] = str(entry.size)
        with LOCK:
            STATS["bytes_to_client"] += 0 if method == "HEAD" else entry.size
        return 200, headers, entry.body

    def passthrough_headers(self, origin_headers, cache_state):
        headers = edge_headers(cache_state)
        for name in ("Content-Type", "ETag", "Last-Modified", "Cache-Control", "Content-Range", "Accept-Ranges"):
            if name.lower() in origin_headers:
                headers[name] = origin_headers[name.lower()]
        return headers

    def store(self, path, origin_headers, body, method="GET"):
        # HEAD 的响应没有 body，不能入缓存（否则会用空 body 覆盖正常副本）
        if method != "GET":
            return
        if not is_cacheable(origin_headers):
            return
        ttl = parse_max_age(origin_headers.get("cache-control"), 0)
        if ttl <= 0:
            return
        CACHE.put(path, CacheEntry(
            body=body,
            etag=origin_headers.get("etag"),
            last_modified=origin_headers.get("last-modified"),
            ttl=ttl,
            content_type=origin_headers.get("content-type", "application/octet-stream"),
        ))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    global EDGE_ID
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default="A", help="边缘节点标识，A / B")
    parser.add_argument("--bind", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--capacity", type=int, default=None)
    parser.add_argument("--delay", type=int, default=None, help="人为链路时延（毫秒）")
    args = parser.parse_args()

    spec = next((e for e in config.EDGES if e["id"] == args.id), None)
    if spec is None:
        print("未知边缘 id：%s（可选 %s）" % (args.id, [e["id"] for e in config.EDGES]))
        sys.exit(2)

    EDGE_ID = args.id
    bind = args.bind or spec["bind"]
    port = args.port or spec["port"]
    CACHE.capacity = args.capacity or spec["capacity"]
    global DELAY_MS
    DELAY_MS = args.delay if args.delay is not None else spec["delay_ms"]

    server = Server((bind, port), Handler)
    print("[edge %s] listening http://%s:%d  origin=http://%s:%d  capacity=%d delay=%dms"
          % (EDGE_ID, bind, port, config.ORIGIN["host"], config.ORIGIN["port"], CACHE.capacity, DELAY_MS),
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("[edge %s] stopped" % EDGE_ID, flush=True)


if __name__ == "__main__":
    main()
