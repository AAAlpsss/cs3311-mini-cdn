"""测量客户端：自己发 DNS 查询 → 自己发 HTTP 请求，记录分阶段耗时。

用法：
  python src/client/client.py --resolve-only          只做一次 DNS 解析，看被分到哪个边缘
  python src/client/client.py --n 20                  均匀请求 20 次并汇总
  python src/client/client.py --n 200 --zipf 0.9      按 Zipf 分布请求（热门倾斜）
  python src/client/client.py --n 20 --edge 127.0.0.1:8000   绕过 DNS 直连某个边缘

输出：results/raw/client-<label>.csv + 终端汇总（命中率 / TTFB）
"""
import argparse
import csv
import os
import random
import socket
import struct
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common import config, console  # noqa: E402
from common.http_util import read_response  # noqa: E402

console.setup()

QTYPE_A = 1
QCLASS_IN = 1


# ------------------------------------------------------------------ DNS 客户端
def dns_query(name, server, port, timeout=2.0):
    qid = random.randint(0, 0xFFFF)
    question = b""
    for label in name.split("."):
        if label:
            question += bytes([len(label)]) + label.encode("latin-1")
    question += b"\x00" + struct.pack(">HH", QTYPE_A, QCLASS_IN)
    packet = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + question

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (server, port))
        data, _ = sock.recvfrom(2048)
    finally:
        sock.close()

    rid, flags, qdcount, ancount = struct.unpack(">HHHH", data[:8])
    if rid != qid or (flags & 0x000F) != 0:
        raise RuntimeError("DNS 应答异常 (rcode=%d)" % (flags & 0x000F))
    if ancount < 1:
        raise RuntimeError("DNS 应答没有 A 记录")

    offset = 12
    for _ in range(qdcount):                     # 跳过问题段
        while data[offset] != 0:
            offset += 1 + data[offset]
        offset += 1 + 4

    ttl = None
    for _ in range(ancount):
        if data[offset] & 0xC0:                  # 压缩指针
            offset += 2
        else:
            while data[offset] != 0:
                offset += 1 + data[offset]
            offset += 1
        rtype, _rclass, ttl, rdlength = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        if rtype == QTYPE_A and rdlength == 4:
            return socket.inet_ntoa(data[offset:offset + 4]), ttl
        offset += rdlength
    raise RuntimeError("没有解析到 A 记录")


# ------------------------------------------------------------------ HTTP 客户端
def http_get(ip, port, path, timeout=5.0):
    """发一个非持久 GET，返回 (status, headers, body, timings)。

    timings 口径：
      connect  从开始到 TCP 建连完成
      ttfb     从写完请求到收到响应第一个字节
      total    从开始到读完 body
    """
    timings = {"connect": 0.0, "ttfb": 0.0, "total": 0.0}
    started = time.perf_counter()
    sock = socket.create_connection((ip, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        timings["connect"] = (time.perf_counter() - started) * 1000
        request = ("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\nAccept: */*\r\n\r\n"
                   % (path, config.ROUTER["domain"]))
        sent = time.perf_counter()
        sock.sendall(request.encode("latin-1"))

        marks = {}

        def note_first_byte():
            marks["first"] = time.perf_counter()

        status, headers, body = read_response(sock, on_first_byte=note_first_byte)
        timings["ttfb"] = (marks.get("first", time.perf_counter()) - sent) * 1000
        timings["total"] = (time.perf_counter() - started) * 1000
    finally:
        sock.close()
    return status, headers, body, timings


def list_objects():
    names = sorted(n for n in os.listdir(config.DATA_DIR) if n.endswith(".txt"))
    return names or ["obj01.txt"]


def zipf_pick(objects, s):
    if s <= 0:
        return random.choice(objects)
    weights = [1.0 / ((i + 1) ** s) for i in range(len(objects))]
    return random.choices(objects, weights=weights, k=1)[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--zipf", type=float, default=0.0, help="0 = 均匀分布；0.9 表示热门倾斜")
    parser.add_argument("--dns", default="%s:%d" % (config.ROUTER["host"], config.ROUTER["port"]))
    parser.add_argument("--edge", default=None, help="绕过 DNS，直连 ip:port")
    parser.add_argument("--objects", default="", help="逗号分隔的对象名，默认取 data/objects/*.txt")
    parser.add_argument("--label", default="run", help="CSV 文件名后缀")
    parser.add_argument("--resolve-only", action="store_true")
    parser.add_argument("--include-revalidated", action="store_true", default=True)
    args = parser.parse_args()

    dns_host, dns_port = args.dns.split(":")
    dns_port = int(dns_port)

    if args.resolve_only:
        started = time.perf_counter()
        ip, ttl = dns_query(config.ROUTER["domain"], dns_host, dns_port)
        elapsed = (time.perf_counter() - started) * 1000
        print("%s -> %s (TTL=%ss, %.1f ms)" % (config.ROUTER["domain"], ip, ttl, elapsed))
        return

    objects = [o for o in args.objects.split(",") if o] or list_objects()
    config.ensure_dirs()
    rows = []
    resolve_ms = 0.0

    for index in range(args.n):
        obj = zipf_pick(objects, args.zipf)
        path = "/" + obj
        if args.edge:
            ip, port = args.edge.split(":")
            port = int(port)
        else:
            started = time.perf_counter()
            ip, _ttl = dns_query(config.ROUTER["domain"], dns_host, dns_port)
            resolve_ms = (time.perf_counter() - started) * 1000
            port = config.EDGE_PORT

        try:
            status, headers, body, timings = http_get(ip, port, path)
        except OSError as exc:
            print("请求失败 %s: %s" % (path, exc))
            continue

        rows.append({
            "seq": index + 1,
            "object": obj,
            "ip": ip,
            "edge": headers.get("x-edge", "-"),
            "cache": headers.get("x-cache", "-"),
            "status": status,
            "resolve_ms": round(resolve_ms, 2),
            "connect_ms": round(timings["connect"], 2),
            "ttfb_ms": round(timings["ttfb"], 2),
            "total_ms": round(timings["total"], 2),
            "bytes": len(body),
        })

    if not rows:
        print("没有成功的请求")
        return

    csv_path = os.path.join(config.RAW_DIR, "client-%s.csv" % args.label)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    hits = [r for r in rows if r["cache"] == "HIT"]
    misses = [r for r in rows if r["cache"] == "MISS"]
    revalidated = [r for r in rows if r["cache"] == "REVALIDATED"]

    def avg(items, key):
        return sum(i[key] for i in items) / len(items) if items else 0.0

    print("\n== 汇总：%d 次请求，用时 %s ==" % (len(rows), os.path.basename(csv_path)))
    print("命中率 %.1f%%  (HIT %d / REVALIDATED %d / MISS %d)"
          % (100.0 * len(hits) / len(rows), len(hits), len(revalidated), len(misses)))
    print("TTFB  HIT %.2f ms | REVALIDATED %.2f ms | MISS %.2f ms"
          % (avg(hits, "ttfb_ms"), avg(revalidated, "ttfb_ms"), avg(misses, "ttfb_ms")))
    per_edge = {}
    for row in rows:
        per_edge[row["edge"]] = per_edge.get(row["edge"], 0) + 1
    print("边缘分布 %s   总字节 %d" % (per_edge, sum(r["bytes"] for r in rows)))


if __name__ == "__main__":
    main()
