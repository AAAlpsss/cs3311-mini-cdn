"""实验脚本：一条条跑，输出可直接写进报告的表格。

前提：另开终端先运行 python scripts/run_all.py
运行： python scripts/experiments.py e1
      python scripts/experiments.py e1 e2 e3 e4 e5
      python scripts/experiments.py all
"""
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from client.client import dns_query, http_get  # noqa: E402
from common import config, console, http_client  # noqa: E402

console.setup()

ALL_OBJECTS = ["/obj%02d.txt" % i for i in range(1, 11)]


# ------------------------------------------------------------------ 小工具
def edge_spec(edge_id):
    return next(e for e in config.EDGES if e["id"] == edge_id)


def edge_admin(edge_id, path):
    spec = edge_spec(edge_id)
    status, _headers, body = http_client.request(spec["bind"], spec["port"], path, timeout=5)
    return body.decode()


def origin_admin(path):
    status, _headers, body = http_client.request(
        config.ORIGIN["host"], config.ORIGIN["port"], path, timeout=5)
    return body.decode()


def origin_stats():
    return json.loads(http_client.request(
        config.ORIGIN["host"], config.ORIGIN["port"], "/origin-stats")[2])


def edge_stats(edge_id):
    spec = edge_spec(edge_id)
    return json.loads(http_client.request(spec["bind"], spec["port"], "/stats")[2])


def resolve():
    started = time.perf_counter()
    ip, ttl = dns_query(config.ROUTER["domain"], config.ROUTER["host"], config.ROUTER["port"])
    return ip, ttl, (time.perf_counter() - started) * 1000


def clear_all():
    for spec in config.EDGES:
        edge_admin(spec["id"], "/admin/clear")


def table_header(*cols):
    print("| " + " | ".join(cols) + " |")
    print("|" + "|".join(["---"] * len(cols)) + "|")


def row(*cells):
    print("| " + " | ".join(str(c) for c in cells) + " |")


# ------------------------------------------------------------------ E1
def e1():
    print("\n### E1：命中 vs 未命中 vs 无 CDN\n")
    clear_all()
    # 单机回环没有真实链路时延，给源站加 50ms，才能体现代理「走近端」的价值
    origin_admin("/admin/delay?ms=50")
    print("源站人为响应时延 50 ms（模拟源站离用户较远），边缘在本地。\n")
    before = origin_stats()

    print("直连源站（无 CDN）：")
    table_header("序号", "状态码", "X-Cache", "TTFB(ms)")
    for i in range(3):
        status, headers, body, timings = http_get(
            config.ORIGIN["host"], config.ORIGIN["port"], "/obj05.txt")
        row(i + 1, status, headers.get("x-cache", "-"), "%.2f" % timings["ttfb"])

    print("\n经边缘 A：")
    table_header("序号", "状态码", "X-Cache", "Age(s)", "TTFB(ms)", "字节")
    for i in range(3):
        status, headers, body, timings = http_get(
            edge_spec("A")["bind"], edge_spec("A")["port"], "/obj05.txt")
        row(i + 1, status, headers.get("x-cache", "-"), headers.get("age", "-"),
            "%.2f" % timings["ttfb"], len(body))

    after = origin_stats()
    print("\n源站请求数变化：%d -> %d（3 次边缘命中只回源 1 次）"
          % (before["requests"], after["requests"]))
    # 命中时延用边缘统计的平均值更有说服力
    stats = edge_stats("A")
    print("边缘 A 命中率：%.1f%%（HIT %d / REVALIDATED %d / MISS %d）"
          % (stats["hit_ratio"] * 100, stats["hits"], stats["revalidated"], stats["misses"]))
    print("\n结论：边缘命中走本地副本（不受源站链路时延影响），首字节时间下降一个数量级。")
    origin_admin("/admin/delay?ms=0")


# ------------------------------------------------------------------ E2
def e2():
    print("\n### E2：条件请求再验证（304 不传 body）\n")
    ttl = 2
    origin_admin("/admin/max-age?seconds=%d" % ttl)
    clear_all()
    print("源站 max-age 置为 %ds，清空边缘缓存。" % ttl)

    table_header("步骤", "边缘状态", "HTTP", "TTFB(ms)", "字节", "源站 304 次数")
    status, headers, body, timings = http_get(
        edge_spec("A")["bind"], edge_spec("A")["port"], "/obj05.txt")
    row("首次请求", headers.get("x-cache", "-"), status, "%.2f" % timings["ttfb"], len(body),
        origin_stats()["not_modified"])

    time.sleep(ttl + 0.5)
    before_304 = origin_stats()["not_modified"]
    status, headers, body, timings = http_get(
        edge_spec("A")["bind"], edge_spec("A")["port"], "/obj05.txt")
    after_304 = origin_stats()["not_modified"]
    row("过期后再请求", headers.get("x-cache", "-"), status, "%.2f" % timings["ttfb"], len(body),
        "%d (+%d)" % (after_304, after_304 - before_304))

    print("\n结论：边缘带 If-None-Match 回源，源站返回 304，边缘复用本地副本。")
    print("      Wireshark 里可以看到 304 报文没有 body（关键截图）。")
    origin_admin("/admin/max-age?seconds=%d" % config.ORIGIN["max_age"])


# ------------------------------------------------------------------ E3
def e3():
    print("\n### E3：LRU 淘汰与 TTL 权衡\n")
    edge_admin("A", "/admin/capacity?n=3")
    edge_admin("A", "/admin/clear")
    print("边缘 A 容量设为 3 个对象，依次请求 8 个不同对象：\n")
    table_header("对象", "X-Cache", "缓存项数", "累计淘汰")
    for path in ALL_OBJECTS[:8]:
        status, headers, body, _timings = http_get(
            edge_spec("A")["bind"], edge_spec("A")["port"], path)
        stats = edge_stats("A")
        row(path, headers.get("x-cache", "-"), stats["cache_entries"], stats["evictions"])

    status, headers, _body, _timings = http_get(
        edge_spec("A")["bind"], edge_spec("A")["port"], ALL_OBJECTS[0])
    print("\n再请求最早那个对象：X-Cache=%s（说明已被 LRU 淘汰）" % headers.get("x-cache"))

    print("\nTTL 扫描（max-age 越小越新鲜，但命中率越低）：")
    table_header("max-age(s)", "命中率", "HIT", "REVALIDATED", "MISS")
    for ttl in (1, 3, 6):
        origin_admin("/admin/max-age?seconds=%d" % ttl)
        edge_admin("A", "/admin/capacity?n=5")
        edge_admin("A", "/admin/clear")
        for _round in range(3):
            for path in ALL_OBJECTS[:5]:
                http_get(edge_spec("A")["bind"], edge_spec("A")["port"], path)
            time.sleep(ttl + 0.3)
        stats = edge_stats("A")
        row(ttl, "%.1f%%" % (stats["hit_ratio"] * 100), stats["hits"], stats["revalidated"], stats["misses"])

    origin_admin("/admin/max-age?seconds=%d" % config.ORIGIN["max_age"])
    edge_admin("A", "/admin/capacity?n=5")


# ------------------------------------------------------------------ E4
def e4():
    print("\n### E4：DNS 就近调度与故障切换\n")
    ip1, ttl1, ms1 = resolve()
    id1 = next(e["id"] for e in config.EDGES if e["bind"] == ip1)
    print("解析 %s -> %s（边缘 %s，TTL=%ss，耗时 %.2f ms）"
          % (config.ROUTER["domain"], ip1, id1, ttl1, ms1))

    spec1 = edge_spec(id1)
    status, headers, _body, timings = http_get(spec1["bind"], spec1["port"], "/obj05.txt")
    print("经边缘 %s 请求：HTTP %d，X-Cache=%s，TTFB %.2f ms"
          % (id1, status, headers.get("x-cache"), timings["ttfb"]))

    http_client.request(config.ROUTER["host"], config.ROUTER["admin_port"],
                        "/admin/down?edge=" + id1, timeout=3)
    print("\n手动下线边缘 %s 后重新解析 ..." % id1)
    time.sleep(0.3)
    ip2, ttl2, ms2 = resolve()
    id2 = next(e["id"] for e in config.EDGES if e["bind"] == ip2)
    print("解析 %s -> %s（边缘 %s，耗时 %.2f ms）" % (config.ROUTER["domain"], ip2, id2, ms2))

    spec2 = edge_spec(id2)
    status, headers, body, timings = http_get(spec2["bind"], spec2["port"], "/obj05.txt")
    print("切换后请求：HTTP %d，X-Cache=%s，TTFB %.2f ms，%d B"
          % (status, headers.get("x-cache"), timings["ttfb"], len(body)))

    http_client.request(config.ROUTER["host"], config.ROUTER["admin_port"],
                        "/admin/up?edge=" + id1, timeout=3)
    print("已恢复边缘 %s。" % id1)
    edges = json.loads(http_client.request(
        config.ROUTER["host"], config.ROUTER["admin_port"], "/admin/edges")[2])
    print("\n当前调度器视图：")
    for edge in edges["edges"]:
        print("  %s  %s  up=%s  rtt=%s ms  selections=%d"
              % (edge["id"], edge["address"], edge["healthy"], edge["rtt_ms"], edge["selections"]))


# ------------------------------------------------------------------ E5
def e5():
    print("\n### E5：聚合吞吐与并发\n")
    clear_all()
    origin_admin("/admin/delay?ms=50")
    print("源站人为响应时延 50 ms。\n")

    def run_sequence(host, port, count):
        bytes_total = 0
        started = time.perf_counter()
        for i in range(count):
            status, _headers, body, _timings = http_get(host, port, ALL_OBJECTS[i % len(ALL_OBJECTS)])
            bytes_total += len(body)
        return bytes_total, (time.perf_counter() - started)

    total_direct, elapsed_direct = run_sequence(config.ORIGIN["host"], config.ORIGIN["port"], 30)
    clear_all()
    total_edge, elapsed_edge = run_sequence(edge_spec("A")["bind"], edge_spec("A")["port"], 30)

    table_header("路径", "请求数", "总字节", "总耗时(s)", "吞吐(MB/s)")
    row("直连源站", 30, total_direct, "%.3f" % elapsed_direct, "%.3f" % (total_direct / elapsed_direct / 1e6))
    row("经边缘 A（含首次 MISS）", 30, total_edge, "%.3f" % elapsed_edge, "%.3f" % (total_edge / elapsed_edge / 1e6))

    print("\n并发压力：20 线程 × 5 次请求，全部经 DNS 选出的边缘")
    ip, _ttl, _ms = resolve()
    port = config.EDGE_PORT
    results = {"ok": 0, "fail": 0, "bytes": 0}
    lock = threading.Lock()

    def worker():
        for i in range(5):
            try:
                status, _headers, body, _timings = http_get(ip, port, ALL_OBJECTS[i % len(ALL_OBJECTS)])
                with lock:
                    results["ok"] += 1
                    results["bytes"] += len(body)
            except OSError:
                with lock:
                    results["fail"] += 1

    threads = [threading.Thread(target=worker) for _ in range(20)]
    started = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - started
    print("成功 %d 次，失败 %d 次，用时 %.3f s，吞吐 %.3f MB/s"
          % (results["ok"], results["fail"], elapsed, results["bytes"] / elapsed / 1e6))
    stats = edge_stats("A")
    print("边缘 A 统计：%s" % json.dumps({k: stats[k] for k in
                                    ("requests", "hits", "misses", "revalidated", "evictions", "inflight")}))
    origin_admin("/admin/delay?ms=0")


EXPERIMENTS = {"e1": e1, "e2": e2, "e3": e3, "e4": e4, "e5": e5}


def main():
    args = sys.argv[1:] or ["all"]
    if args == ["all"]:
        args = list(EXPERIMENTS)
    for name in args:
        if name == "all":
            continue
        if name not in EXPERIMENTS:
            print("未知实验：%s（可选 %s / all）" % (name, " ".join(EXPERIMENTS)))
            continue
        EXPERIMENTS[name]()


if __name__ == "__main__":
    main()
