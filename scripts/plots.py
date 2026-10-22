"""从 results/raw/*.csv 出图（报告用的 6 张图）。

运行： python scripts/plots.py
前提： 先跑 python scripts/experiments.py all
产物： results/figures/fig{1..6}_*.png
"""
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common import config, console  # noqa: E402

console.setup()

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

FIG_DIR = os.path.join(config.RESULTS_DIR, "figures")
CSV_DIR = config.RAW_DIR


def read_csv(name):
    path = os.path.join(CSV_DIR, name)
    if not os.path.isfile(path):
        print("跳过 %s（缺 %s）" % (name, os.path.relpath(path, ROOT)))
        return []
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def save(fig, name):
    os.makedirs(FIG_DIR, exist_ok=True)
    path = os.path.join(FIG_DIR, name)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    print("  -> %s" % os.path.relpath(path, ROOT))


def avg(values):
    return sum(values) / len(values) if values else 0.0


# ------------------------------------------------------------------ 图 1
def fig_e1(rows):
    if not rows:
        return
    direct = [float(r["ttfb_ms"]) for r in rows if r["path"] == "origin_direct"]
    miss = [float(r["ttfb_ms"]) for r in rows if r["path"] == "edge_a" and r["cache"] == "MISS"]
    hit = [float(r["ttfb_ms"]) for r in rows if r["path"] == "edge_a" and r["cache"] == "HIT"]
    labels, values = [], []
    if direct:
        labels.append("直连源站\n(50 ms 链路)")
        values.append(avg(direct))
    if miss:
        labels.append("边缘 MISS\n(回源)")
        values.append(avg(miss))
    if hit:
        labels.append("边缘 HIT\n(本地副本)")
        values.append(avg(hit))
    fig, ax = plt.subplots(figsize=(6, 3.6))
    bars = ax.bar(labels, values, color=["#9aa5b1", "#f2994a", "#2f80ed"])
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value, "%.2f ms" % value,
                ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("首字节时间 TTFB (ms)")
    ax.set_title("E1 命中与否的首字节时间（源站注入 50 ms 时延）")
    ax.grid(axis="y", alpha=0.3)
    save(fig, "fig1_e1_ttfb.png")


# ------------------------------------------------------------------ 图 2
def fig_e2(rows):
    if len(rows) < 2:
        return
    labels = [r["step"] for r in rows]
    ttfb = [float(r["ttfb_ms"]) for r in rows]
    fig, ax = plt.subplots(figsize=(5.6, 3.6))
    bars = ax.bar(labels, ttfb, color=["#2f80ed", "#27ae60"])
    for bar, r in zip(bars, rows):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                "%s\n%.2f ms" % (r["cache"], float(r["ttfb_ms"])), ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("首字节时间 TTFB (ms)")
    ax.set_title("E2 过期后再验证：304 复用本地副本")
    ax.grid(axis="y", alpha=0.3)
    save(fig, "fig2_e2_revalidate.png")


# ------------------------------------------------------------------ 图 3
def fig_e3_lru(rows):
    if not rows:
        return
    index = list(range(1, len(rows) + 1))
    entries = [int(r["entries"]) for r in rows]
    evictions = [int(r["evictions"]) for r in rows]
    fig, ax = plt.subplots(figsize=(6, 3.6))
    ax.plot(index, entries, marker="o", color="#2f80ed", label="缓存项数（容量 3）")
    ax.plot(index, evictions, marker="s", color="#eb5757", label="累计淘汰数")
    ax.set_xlabel("第 n 个不同对象的请求")
    ax.set_ylabel("数量")
    ax.set_title("E3 LRU 淘汰过程")
    ax.set_xticks(index)
    ax.grid(alpha=0.3)
    ax.legend()
    save(fig, "fig3_e3_lru.png")


# ------------------------------------------------------------------ 图 4
def fig_e3_ttl(rows):
    if not rows:
        return
    labels = ["%s s" % r["max_age_s"] for r in rows]
    values = [float(r["hit_ratio"]) * 100 for r in rows]
    fig, ax = plt.subplots(figsize=(5.6, 3.6))
    bars = ax.bar(labels, values, color="#2f80ed")
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value, "%.1f%%" % value,
                ha="center", va="bottom", fontsize=9)
    ax.set_xlabel("源站下发的 Cache-Control: max-age")
    ax.set_ylabel("边缘命中率 (%)")
    ax.set_ylim(0, 100)
    ax.set_title("E3 TTL 与命中率的权衡")
    ax.grid(axis="y", alpha=0.3)
    save(fig, "fig4_e3_ttl.png")


# ------------------------------------------------------------------ 图 5
def fig_e4(rows):
    if len(rows) < 2:
        return
    labels = ["切换前\n边缘 %s" % rows[0]["edge"], "切换后\n边缘 %s" % rows[1]["edge"]]
    ttfb = [float(r["ttfb_ms"]) for r in rows]
    fig, ax = plt.subplots(figsize=(5.6, 3.6))
    bars = ax.bar(labels, ttfb, color=["#2f80ed", "#f2994a"])
    for bar, r in zip(bars, rows):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                "%s\n%.2f ms" % (r["resolved_ip"], float(r["ttfb_ms"])), ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("首字节时间 TTFB (ms)")
    ax.set_title("E4 边缘下线后 DNS 重新调度，请求仍成功")
    ax.grid(axis="y", alpha=0.3)
    save(fig, "fig5_e4_failover.png")


# ------------------------------------------------------------------ 图 6
def fig_e5(rows):
    if not rows:
        return
    labels = {"origin_direct": "直连源站\n(99 次)",
              "edge_a": "经边缘\n(99 次, 已预热)",
              "edge_concurrent_20x5": "边缘并发\n(20×5)"}
    names = [labels.get(r["path"], r["path"]) for r in rows]
    values = [float(r["mbps"]) for r in rows]
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    bars = ax.bar(names, values, color=["#9aa5b1", "#2f80ed", "#27ae60"])
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value, "%.2f MB/s" % value,
                ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("吞吐 (MB/s)")
    ax.set_title("E5 命中后聚合吞吐（源站注入 50 ms 时延，热点对象）")
    ax.grid(axis="y", alpha=0.3)
    save(fig, "fig6_e5_throughput.png")


def main():
    fig_e1(read_csv("e1-hit-vs-miss.csv"))
    fig_e2(read_csv("e2-revalidate.csv"))
    fig_e3_lru(read_csv("e3-lru.csv"))
    fig_e3_ttl(read_csv("e3-ttl.csv"))
    fig_e4(read_csv("e4-failover.csv"))
    fig_e5(read_csv("e5-throughput.csv"))
    print("图已输出到 %s" % os.path.relpath(FIG_DIR, ROOT))


if __name__ == "__main__":
    main()
