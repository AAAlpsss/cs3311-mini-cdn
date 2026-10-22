"""DNS 调度器：把域名解析到「最近且不忙」的边缘节点，并用健康探测做故障切换。

运行： python src/router/dns_router.py
查询： python src/client/client.py --resolve-only         （自带 DNS 客户端，无需 dig）
      dig @127.0.0.1 -p 5353 www.cdn.test                （装了 dig 也行）
观测： GET http://127.0.0.1:8053/admin/edges             各边缘 RTT / 健康 / 被选中次数
控制： GET http://127.0.0.1:8053/admin/down?edge=A       手动下线（实验 E4）
      GET http://127.0.0.1:8053/admin/up?edge=A         恢复
"""
import json
import os
import socket
import socketserver
import struct
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common import config, console, http_client  # noqa: E402
from common.http_util import build_response, read_request  # noqa: E402

console.setup()

QTYPE_A = 1
QTYPE_AAAA = 28
QCLASS_IN = 1
FAILURE_THRESHOLD = 2      # 连续探测失败多少次才判为不可用（防抖动）


class EdgeState:
    def __init__(self, spec):
        self.id = spec["id"]
        self.host = spec["bind"]
        self.port = spec["port"]
        self.rtt_ms = float("inf")
        self.inflight = 0
        self.healthy = False          # 探测结果
        self.forced_down = False      # 人工下线（实验用）
        self.selections = 0
        self.probes = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.last_error = "not probed yet"

    def available(self):
        return self.healthy and not self.forced_down

    def score(self):
        """越小越优：RTT 为主，活跃请求数做惩罚。"""
        base = self.rtt_ms if self.rtt_ms != float("inf") else 1e6
        return base * (1.0 + config.ROUTER["load_weight"] * self.inflight)

    def as_dict(self):
        return {
            "id": self.id,
            "address": "%s:%d" % (self.host, self.port),
            "rtt_ms": None if self.rtt_ms == float("inf") else round(self.rtt_ms, 2),
            "inflight": self.inflight,
            "healthy": self.healthy,
            "forced_down": self.forced_down,
            "available": self.available(),
            "selections": self.selections,
            "probes": self.probes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
        }


EDGES = [EdgeState(spec) for spec in config.EDGES]
STATE_LOCK = threading.Lock()
ROUND_ROBIN = {"cursor": 0}
QUERY_COUNT = {"total": 0, "served": 0, "refused": 0}


def log(message):
    print("[router] %s" % message, flush=True)


# --------------------------------------------------------------- 选路
def choose_edge():
    with STATE_LOCK:
        candidates = [e for e in EDGES if e.available()]
        if not candidates:
            # 全挂了也要应答（返回一个 down 的节点，客户端会看到 502，便于演示故障域）
            candidates = [e for e in EDGES if not e.forced_down] or list(EDGES)
        cursor = ROUND_ROBIN["cursor"]
        ROUND_ROBIN["cursor"] = (cursor + 1) % len(candidates)
        # 先按评分排，评分相同（例如都在同一台机器上）时用轮询打散
        ordered = sorted(candidates, key=lambda e: (round(e.score(), 1),))
        best = ordered[0]
        if len(ordered) > 1 and abs(ordered[0].score() - ordered[1].score()) < 1e-9:
            best = ordered[cursor % len(ordered)]
        best.selections += 1
        return best


def probe_once(edge):
    started = time.perf_counter()
    try:
        status, _headers, body = http_client.request(
            edge.host, edge.port, "/stats", timeout=config.ROUTER["probe_timeout"])
        rtt_ms = (time.perf_counter() - started) * 1000.0
        if status != 200:
            raise ConnectionError("probe status %d" % status)
        payload = json.loads(body.decode() or "{}")
        with STATE_LOCK:
            edge.rtt_ms = rtt_ms
            edge.inflight = int(payload.get("inflight", 0))
            edge.healthy = True
            edge.consecutive_failures = 0
            edge.probes += 1
            edge.last_error = ""
    except Exception as exc:  # noqa: BLE001 探测失败的原因很多，统一计数
        with STATE_LOCK:
            edge.consecutive_failures += 1
            edge.probes += 1
            edge.failures += 1
            edge.last_error = str(exc)
            # 防抖动：连续 2 次失败才判为不可用，避免瞬时抖动导致误切换
            if edge.consecutive_failures >= FAILURE_THRESHOLD:
                edge.healthy = False
                edge.rtt_ms = float("inf")


def probe_loop():
    while True:
        for edge in EDGES:
            probe_once(edge)
        time.sleep(config.ROUTER["probe_interval"])


# --------------------------------------------------------------- DNS 报文
def parse_question(data):
    """返回 (qname, qtype, qclass, question_end_offset)；报文非法返回 None。"""
    if len(data) < 12:
        return None
    _qid, _flags, qdcount = struct.unpack(">HHH", data[:6])
    if qdcount < 1:
        return None
    offset = 12
    labels = []
    while offset < len(data):
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0:      # 问题段不使用压缩指针
            return None
        offset += 1
        labels.append(data[offset:offset + length].decode("latin-1"))
        offset += length
    if offset + 4 > len(data):
        return None
    qtype, qclass = struct.unpack(">HH", data[offset:offset + 4])
    return ".".join(labels), qtype, qclass, offset + 4


def encode_name(name):
    out = b""
    for label in name.split("."):
        if not label:
            continue
        if len(label) > 63:
            raise ValueError("label too long")
        out += bytes([len(label)]) + label.encode("latin-1")
    return out + b"\x00"


def build_answer(query, ip_text, ttl):
    question = query[12:parse_question(query)[3]]
    header = struct.pack(">HHHHHH", struct.unpack(">H", query[:2])[0], 0x8180, 1, 1, 0, 0)
    answer = (b"\xc0\x0c"                                  # 指向问题里的域名
              + struct.pack(">HHIH", QTYPE_A, QCLASS_IN, ttl, 4)
              + socket.inet_aton(ip_text))
    return header + question + answer


def build_error(query, rcode=3):
    header = struct.pack(">HHHHHH", struct.unpack(">H", query[:2])[0], 0x8180 | rcode, 1, 0, 0, 0)
    parsed = parse_question(query)
    question = query[12:parsed[3]] if parsed else b""
    return header + question


def dns_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((config.ROUTER["host"], config.ROUTER["port"]))
    log("DNS listening udp %s:%d for %s"
        % (config.ROUTER["host"], config.ROUTER["port"], config.ROUTER["domain"]))

    while True:
        try:
            query, addr = sock.recvfrom(1500)
        except OSError:
            continue
        parsed = parse_question(query)
        with STATE_LOCK:
            QUERY_COUNT["total"] += 1
        if not parsed:
            with STATE_LOCK:
                QUERY_COUNT["refused"] += 1
            continue

        qname, qtype, qclass, _end = parsed
        if qname.rstrip(".").lower() != config.ROUTER["domain"].lower() or qtype not in (QTYPE_A, QTYPE_AAAA):
            sock.sendto(build_error(query, rcode=3), addr)
            with STATE_LOCK:
                QUERY_COUNT["refused"] += 1
            continue

        edge = choose_edge()
        reply = build_answer(query, edge.host, config.ROUTER["ttl"])
        sock.sendto(reply, addr)
        with STATE_LOCK:
            QUERY_COUNT["served"] += 1
        log("answer %s -> %s (edge %s, rtt=%s ms)"
            % (qname, edge.host, edge.id,
               "n/a" if edge.rtt_ms == float("inf") else "%.1f" % edge.rtt_ms))


# --------------------------------------------------------------- 控制台（HTTP）
class AdminHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            request = read_request(self.request)
        except (socket.timeout, OSError, ValueError):
            return
        if request is None:
            return
        method, target, _version, _headers, _body = request
        path, _, query = target.partition("?")
        params = dict(p.split("=") for p in query.split("&") if "=" in p)
        status, payload = self.route(path, params)
        try:
            self.request.sendall(build_response(status, {"Content-Type": "application/json"}, payload, method, False))
        except OSError:
            pass

    def route(self, path, params):
        if path == "/admin/edges":
            with STATE_LOCK:
                payload = {"domain": config.ROUTER["domain"], "ttl": config.ROUTER["ttl"],
                           "edges": [e.as_dict() for e in EDGES], "queries": dict(QUERY_COUNT)}
            return 200, json.dumps(payload, indent=2).encode()
        if path in ("/admin/down", "/admin/up"):
            target_id = (params.get("edge") or "").upper()
            with STATE_LOCK:
                matched = [e for e in EDGES if e.id == target_id]
                if not matched:
                    return 404, b'{"error":"unknown edge"}'
                for edge in matched:
                    edge.forced_down = (path == "/admin/down")
            log("manual %s edge %s" % ("DOWN" if path == "/admin/down" else "UP", target_id))
            return 200, b'{"ok":true}'
        if path == "/health":
            return 200, b'{"ok":true}'
        return 404, b'{"error":"not found"}'


class AdminServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    threading.Thread(target=probe_loop, daemon=True).start()
    admin = AdminServer((config.ROUTER["host"], config.ROUTER["admin_port"]), AdminHandler)
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    log("admin console http://%s:%d/admin/edges"
        % (config.ROUTER["host"], config.ROUTER["admin_port"]))
    try:
        dns_loop()
    except KeyboardInterrupt:
        pass
    finally:
        admin.shutdown()
        log("stopped")


if __name__ == "__main__":
    main()
