"""现场演示脚本：一条命令跑完所有要点，并打印可直接写进报告的观测结果。

前提：另开终端先运行 python scripts/run_all.py
运行： python scripts/demo.py
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from client.client import dns_query, http_get  # noqa: E402
from common import config, console, http_client  # noqa: E402

console.setup()

OBJECT = "obj05.txt"


def edge_spec(edge_id):
    return next(e for e in config.EDGES if e["id"] == edge_id)


def admin(path, params=""):
    url = "/admin/" + path + (("?" + params) if params else "")
    status, _headers, body = http_client.request(
        config.ROUTER["host"], config.ROUTER["admin_port"], url, timeout=3)
    return status, body.decode()


def origin_admin(path):
    status, _headers, body = http_client.request(
        config.ORIGIN["host"], config.ORIGIN["port"], path, timeout=3)
    return body.decode()


def get_via_edge(edge_id, path="/" + OBJECT):
    spec = edge_spec(edge_id)
    status, headers, body, timings = http_get(spec["bind"], spec["port"], path)
    return status, headers, body, timings


def show(label, status, headers, body, timings):
    print("  %-22s status=%d  X-Cache=%-11s edge=%-2s  %8d B  ttfb=%6.2f ms"
          % (label, status, headers.get("x-cache", "-"), headers.get("x-edge", "-"),
             len(body), timings["ttfb"]))
    if headers.get("age"):
        print("  %-22s Age=%s s（副本已存活秒数）" % ("", headers["age"]))


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main():
    for spec in config.EDGES:
        http_client.request(spec["bind"], spec["port"], "/admin/clear")
        http_client.request(spec["bind"], spec["port"], "/admin/delay?ms=0")
    # 单机回环没有链路时延：给源站加 50ms，代表「源站离用户很远」，边缘在本地
    origin_admin("/admin/delay?ms=50")
    origin_admin("/admin/reset-stats")
    http_get(config.ORIGIN["host"], config.ORIGIN["port"], "/" + OBJECT)   # 预热，避免首个请求含冷启动开销
    origin_admin("/admin/reset-stats")
    print("\n（为逼近真实场景，源站响应人为加了 50 ms 时延，边缘节点在本地）")

    section("1. 没有 CDN：直连源站")
    for i in range(2):
        status, headers, body, timings = http_get(config.ORIGIN["host"], config.ORIGIN["port"], "/" + OBJECT)
        show("直连源站 #%d" % (i + 1), status, headers, body, timings)

    section("2. 有 CDN：缓存未命中 -> 命中")
    for i in range(3):
        status, headers, body, timings = get_via_edge("A")
        show("经边缘 A #%d" % (i + 1), status, headers, body, timings)

    section("3. 条件请求再验证（等 max-age 过期后再请求）")
    print("  等待 %d 秒让边缘副本过期 ..." % (config.ORIGIN["max_age"] + 1))
    time.sleep(config.ORIGIN["max_age"] + 1)
    status, headers, body, timings = get_via_edge("A")
    show("过期后再请求", status, headers, body, timings)
    _s, _h, stats = http_client.request(config.ORIGIN["host"], config.ORIGIN["port"], "/origin-stats")
    print("  源站统计：%s" % stats.decode())

    section("4. DNS 就近调度")
    ip_a, ttl = dns_query(config.ROUTER["domain"], config.ROUTER["host"], config.ROUTER["port"])
    print("  %s -> %s (TTL=%ss)" % (config.ROUTER["domain"], ip_a, ttl))
    print("  控制台：%s" % admin("edges")[1][:400].replace("\n", " "))

    section("5. 故障切换：把当前被选中的边缘下线")
    target = next(e for e in config.EDGES if e["bind"] == ip_a)
    print("  下线边缘 %s ..." % target["id"])
    admin("down", "edge=" + target["id"])
    time.sleep(0.5)
    ip_b, ttl = dns_query(config.ROUTER["domain"], config.ROUTER["host"], config.ROUTER["port"])
    print("  再次解析 %s -> %s" % (config.ROUTER["domain"], ip_b))
    if ip_b != ip_a:
        print("  ✔ 调度器已把请求切到另一个边缘")
    spec_b = next(e for e in config.EDGES if e["bind"] == ip_b)
    status, headers, body, timings = http_get(spec_b["bind"], spec_b["port"], "/" + OBJECT)
    show("切换后请求", status, headers, body, timings)
    admin("up", "edge=" + target["id"])

    section("6. 汇总")
    for spec in config.EDGES:
        status, _h, body = http_client.request(spec["bind"], spec["port"], "/stats")
        data = json.loads(body)
        print("  边缘 %s: 请求 %d, HIT %d, REVALIDATED %d, MISS %d, 命中率 %.1f%%, 回源字节 %d"
              % (spec["id"], data["requests"], data["hits"], data["revalidated"], data["misses"],
                 data["hit_ratio"] * 100, data["bytes_from_origin"]))
    _s, _h, stats = http_client.request(config.ORIGIN["host"], config.ORIGIN["port"], "/origin-stats")
    print("  源站: %s" % stats.decode())
    origin_admin("/admin/delay?ms=0")
    print("\n把上面的输出贴进 report.md 的第 6 节（实验与结果）。")


if __name__ == "__main__":
    main()
