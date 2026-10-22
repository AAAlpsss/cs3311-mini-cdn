"""实验脚本：跑数据 + 落 CSV（报告里的每个数字都能在 results/raw 找到）。

前提：另开终端先运行 python scripts/run_all.py
运行： python scripts/experiments.py all
      python scripts/experiments.py e1 e4
"""
import csv
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
HOT_OBJECTS = ["/obj01.txt", "/obj05.txt", "/obj09.txt"]


# ------------------------------------------------------------------ 小工具
def edge_spec(edge_id):
    return next(e for e in config.EDGES if e["id"] == edge_id)


def edge_admin(edge_id, path):
    spec = edge_spec(edge_id)
    return http_client.request(spec["bind"], spec["port"], path, timeout=5)[2].decode()


def origin_admin(path):
    return http_client.request(config.ORIGIN["host"], config.ORIGIN["port"], path, timeout=5)[2].decode()


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


def reset_stats():
    """清空统计计数器（不清缓存），保证每组实验的比率是干净的。"""
    for spec in config.EDGES:
        edge_admin(spec["id"], "/admin/reset-stats")
    origin_admin("/admin/reset-stats")


def write_csv(name, rows):
    config.ensure_dirs()
    if not rows:
        return None
    path = os.path.join(config.RAW_DIR, name)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print("  -> 数据已写入 %s（%d 行）" % (os.path.relpath(path, ROOT), len(rows)))
    return path


def table_header(*cols):
    print("| " + " | ".join(cols) + " |")
    print("|" + "|".join(["---"] * len(cols)) + "|")


def row(*cells):
    print("| " + " | ".join(str(c) for c in cells) + " |")


def get(host, port, path):
    return http_get(host, port, path)


# ------------------------------------------------------------------ E1
def e1():
    print("\n### E1：无 CDN vs 边缘 MISS vs 边缘 HIT\n")
    clear_all()
    reset_stats()
    origin_admin("/admin/delay?ms=50")
    print("源站人为响应时延 50 ms（模拟源站远离用户），边缘在本地。\n")
    before = origin_stats()
    rows = []

    print("直连源站：")
    table_header("序号", "状态码", "X-Cache", "TTFB(ms)", "字节")
    for i in range(5):
        status, headers, body, timings = get(config.ORIGIN["host"], config.ORIGIN["port"], "/obj05.txt")
        row(i + 1, status, "-", "%.2f" % timings["ttfb"], len(body))
        rows.append({"path": "origin_direct", "seq": i + 1, "status": status, "cache": "-",
                     "ttfb_ms": round(timings["ttfb"], 2), "total_ms": round(timings["total"], 2),
                     "bytes": len(body)})

    print("\n经边缘 A：")
    table_header("序号", "状态码", "X-Cache", "Age(s)", "TTFB(ms)", "字节")
    for i in range(5):
        status, headers, body, timings = get(edge_spec("A")["bind"], edge_spec("A")["port"], "/obj05.txt")
        row(i + 1, status, headers.get("x-cache"), headers.get("age", "-"),
            "%.2f" % timings["ttfb"], len(body))
        rows.append({"path": "edge_a", "seq": i + 1, "status": status, "cache": headers.get("x-cache"),
                     "ttfb_ms": round(timings["ttfb"], 2), "total_ms": round(timings["total"], 2),
                     "bytes": len(body)})

    after = origin_stats()
    print("\n源站请求数变化：%d -> %d（5 次边缘请求只回源 1 次）" % (before["requests"], after["requests"]))
    stats = edge_stats("A")
    print("边缘 A 累计命中率：%.1f%%（HIT %d / REVALIDATED %d / MISS %d）"
          % (stats["hit_ratio"] * 100, stats["hits"], stats["revalidated"], stats["misses"]))
    print("\n结论：边缘 HIT 不经过「用户—源站」链路，TTFB 下降约两个数量级；"
          "源站只需承担 1 次回源。")
    write_csv("e1-hit-vs-miss.csv", rows)
    origin_admin("/admin/delay?ms=0")


# ------------------------------------------------------------------ E2
def e2():
    print("\n### E2：条件请求再验证（304 不传 body）\n")
    ttl = 2
    origin_admin("/admin/max-age?seconds=%d" % ttl)
    clear_all()
    print("源站 max-age=%ds，清空边缘缓存。\n" % ttl)

    rows = []
    status, headers, body, timings = get(edge_spec("A")["bind"], edge_spec("A")["port"], "/obj05.txt")
    not_modified = origin_stats()["not_modified"]
    rows.append({"step": "首次请求", "cache": headers.get("x-cache"), "status": status,
                 "ttfb_ms": round(timings["ttfb"], 2), "bytes": len(body), "origin_not_modified": not_modified})

    time.sleep(ttl + 0.5)
    status, headers, body, timings = get(edge_spec("A")["bind"], edge_spec("A")["port"], "/obj05.txt")
    not_modified = origin_stats()["not_modified"]
    rows.append({"step": "过期后再请求", "cache": headers.get("x-cache"), "status": status,
                 "ttfb_ms": round(timings["ttfb"], 2), "bytes": len(body), "origin_not_modified": not_modified})

    table_header("步骤", "边缘状态", "HTTP", "TTFB(ms)", "传给客户端的字节", "源站累计 304")
    for item in rows:
        row(item["step"], item["cache"], item["status"], "%.2f" % item["ttfb_ms"],
            item["bytes"], item["origin_not_modified"])

    print("\n源站 304 次数 +%d；客户端仍拿到完整 %d B —— 回源链路只传了响应头。"
          % (rows[1]["origin_not_modified"] - rows[0]["origin_not_modified"], rows[1]["bytes"]))
    print("Wireshark 抓包要点：第二次回源请求带 If-None-Match，响应 304 且无 body。")
    write_csv("e2-revalidate.csv", rows)
    origin_admin("/admin/max-age?seconds=%d" % config.ORIGIN["max_age"])


# ------------------------------------------------------------------ E3
def e3():
    print("\n### E3：LRU 淘汰与 TTL 权衡\n")
    edge_admin("A", "/admin/capacity?n=3")
    edge_admin("A", "/admin/clear")
    reset_stats()
    print("边缘 A 容量设为 3，依次请求 8 个不同对象：\n")
    table_header("对象", "X-Cache", "缓存项数", "累计淘汰")
    lru_rows = []
    for path in ALL_OBJECTS[:8]:
        _status, headers, _body, _timings = get(edge_spec("A")["bind"], edge_spec("A")["port"], path)
        stats = edge_stats("A")
        row(path, headers.get("x-cache"), stats["cache_entries"], stats["evictions"])
        lru_rows.append({"request": path, "cache": headers.get("x-cache"),
                         "entries": stats["cache_entries"], "evictions": stats["evictions"]})

    _status, headers, _body, _timings = get(edge_spec("A")["bind"], edge_spec("A")["port"], ALL_OBJECTS[0])
    print("\n再请求最早那个对象 %s：X-Cache=%s（说明已被 LRU 淘汰）" % (ALL_OBJECTS[0], headers.get("x-cache")))
    write_csv("e3-lru.csv", lru_rows)

    print("\nTTL 扫描（请求间隔固定 2 s，只有 max-age 在变）：")
    table_header("max-age(s)", "HIT", "REVALIDATED", "MISS", "命中率")
    ttl_rows = []
    for ttl in (1, 2, 4, 8):
        origin_admin("/admin/max-age?seconds=%d" % ttl)
        edge_admin("A", "/admin/capacity?n=5")
        edge_admin("A", "/admin/clear")
        reset_stats()
        for _round in range(4):
            for path in ALL_OBJECTS[:5]:
                get(edge_spec("A")["bind"], edge_spec("A")["port"], path)
            time.sleep(2.0)
        stats = edge_stats("A")
        row(ttl, stats["hits"], stats["revalidated"], stats["misses"], "%.1f%%" % (stats["hit_ratio"] * 100))
        ttl_rows.append({"max_age_s": ttl, "hits": stats["hits"], "revalidated": stats["revalidated"],
                         "misses": stats["misses"], "hit_ratio": round(stats["hit_ratio"], 4)})
    write_csv("e3-ttl.csv", ttl_rows)

    print("\n结论：容量不足时 LRU 会淘汰「还有效但最近未访问」的对象（第 4 个对象起开始淘汰）；"
          "max-age 小于请求间隔时每次都要回源再验证、命中率塌到 0；"
          "max-age 足够长则命中率上升，代价是内容可能变旧 —— 这就是 TTL 的基本权衡。")
    origin_admin("/admin/max-age?seconds=%d" % config.ORIGIN["max_age"])
    edge_admin("A", "/admin/capacity?n=5")


# ------------------------------------------------------------------ E4
def e4():
    print("\n### E4：DNS 就近调度与故障切换\n")
    ip1, ttl1, ms1 = resolve()
    id1 = next(e["id"] for e in config.EDGES if e["bind"] == ip1)
    spec1 = edge_spec(id1)
    status, headers, _body, timings = get(spec1["bind"], spec1["port"], "/obj05.txt")
    print("解析 %s -> %s（边缘 %s，TTL=%ss，解析耗时 %.2f ms）"
          % (config.ROUTER["domain"], ip1, id1, ttl1, ms1))
    print("经边缘 %s 请求：HTTP %d，X-Cache=%s，TTFB %.2f ms"
          % (id1, status, headers.get("x-cache"), timings["ttfb"]))

    http_client.request(config.ROUTER["host"], config.ROUTER["admin_port"],
                        "/admin/down?edge=" + id1, timeout=3)
    print("\n手动下线边缘 %s，等待调度器探测反映 ..." % id1)
    time.sleep(0.3)
    ip2, ttl2, ms2 = resolve()
    id2 = next(e["id"] for e in config.EDGES if e["bind"] == ip2)
    status2, headers2, body2, timings2 = get(edge_spec(id2)["bind"], edge_spec(id2)["port"], "/obj05.txt")
    print("重新解析 -> %s（边缘 %s，耗时 %.2f ms）；请求 HTTP %d，X-Cache=%s，TTFB %.2f ms，%d B"
          % (ip2, id2, ms2, status2, headers2.get("x-cache"), timings2["ttfb"], len(body2)))

    rows = [
        {"phase": "before_failover", "resolved_ip": ip1, "edge": id1, "resolve_ms": round(ms1, 2),
         "status": status, "cache": headers.get("x-cache"), "ttfb_ms": round(timings["ttfb"], 2)},
        {"phase": "after_failover", "resolved_ip": ip2, "edge": id2, "resolve_ms": round(ms2, 2),
         "status": status2, "cache": headers2.get("x-cache"), "ttfb_ms": round(timings2["ttfb"], 2)},
    ]
    write_csv("e4-failover.csv", rows)

    http_client.request(config.ROUTER["host"], config.ROUTER["admin_port"],
                        "/admin/up?edge=" + id1, timeout=3)
    print("\n已恢复边缘 %s。当前调度器视图：" % id1)
    edges = json.loads(http_client.request(
        config.ROUTER["host"], config.ROUTER["admin_port"], "/admin/edges")[2])
    for edge in edges["edges"]:
        print("  %s  %s  healthy=%s  rtt=%s ms  selections=%d  consecutive_failures=%d"
              % (edge["id"], edge["address"], edge["healthy"], edge["rtt_ms"],
                 edge["selections"], edge["consecutive_failures"]))


# ------------------------------------------------------------------ E5
def e5():
    print("\n### E5：命中带来的聚合吞吐（新设计：先预热，再打热点对象）\n")
    origin_admin("/admin/delay?ms=50")
    clear_all()
    reset_stats()
    print("源站人为响应时延 50 ms。每个对象先预热 1 次，再重复请求 33 次（共 99 次）。\n")
    for path in HOT_OBJECTS:
        get(edge_spec("A")["bind"], edge_spec("A")["port"], path)   # 预热

    def run(host, port):
        total = 0
        started = time.perf_counter()
        for _ in range(33):
            for path in HOT_OBJECTS:
                _status, _headers, body = http_client.request(host, port, path, timeout=10)
                total += len(body)
        return total, time.perf_counter() - started

    direct_bytes, direct_s = run(config.ORIGIN["host"], config.ORIGIN["port"])
    edge_bytes, edge_s = run(edge_spec("A")["bind"], edge_spec("A")["port"])
    stats = edge_stats("A")

    rows = [
        {"path": "origin_direct", "requests": 99, "bytes": direct_bytes,
         "seconds": round(direct_s, 3), "mbps": round(direct_bytes / direct_s / 1e6, 3)},
        {"path": "edge_a", "requests": 99, "bytes": edge_bytes,
         "seconds": round(edge_s, 3), "mbps": round(edge_bytes / edge_s / 1e6, 3)},
    ]

    print("并发压力：20 线程 × 5 次请求（随机热点对象）")
    ip, _ttl, _ms = resolve()
    port = config.EDGE_PORT
    result = {"ok": 0, "fail": 0, "bytes": 0}
    lock = threading.Lock()

    def worker():
        for i in range(5):
            try:
                _status, _headers, body, _timings = http_get(ip, port, HOT_OBJECTS[i % len(HOT_OBJECTS)])
                with lock:
                    result["ok"] += 1
                    result["bytes"] += len(body)
            except OSError:
                with lock:
                    result["fail"] += 1

    threads = [threading.Thread(target=worker) for _ in range(20)]
    started = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    concurrent_s = time.perf_counter() - started
    rows.append({"path": "edge_concurrent_20x5", "requests": result["ok"], "bytes": result["bytes"],
                 "seconds": round(concurrent_s, 3), "mbps": round(result["bytes"] / concurrent_s / 1e6, 3)})

    table_header("路径", "请求数", "总字节", "总耗时(s)", "吞吐(MB/s)")
    for item in rows:
        row(item["path"], item["requests"], item["bytes"], item["seconds"], item["mbps"])

    print("\n边缘 A 统计：HIT %d / REVALIDATED %d / MISS %d，命中率 %.1f%%；"
          "并发请求失败 %d 次。"
          % (stats["hits"], stats["revalidated"], stats["misses"], stats["hit_ratio"] * 100, result["fail"]))
    print("结论：命中后请求不再跨越「用户—源站」这段时延，聚合吞吐提升；"
          "并发下缓存节点成为瓶颈才是真正需要优化的地方。")
    write_csv("e5-throughput.csv", rows)
    origin_admin("/admin/delay?ms=0")


EXPERIMENTS = {"e1": e1, "e2": e2, "e3": e3, "e4": e4, "e5": e5}


def main():
    args = sys.argv[1:] or ["all"]
    if "all" in args:
        args = list(EXPERIMENTS)
    for name in args:
        if name not in EXPERIMENTS:
            print("未知实验：%s（可选 %s / all）" % (name, " ".join(EXPERIMENTS)))
            continue
        EXPERIMENTS[name]()


if __name__ == "__main__":
    main()
