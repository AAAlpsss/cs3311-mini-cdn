"""一键启动全拓扑：源站 + 2 个边缘 + DNS 调度器。

运行： python scripts/run_all.py
停止： Ctrl+C（会一起收掉子进程）
日志： results/logs/{origin,edgeA,edgeB,router}.log
"""
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common import config, console  # noqa: E402

console.setup()

PROCS = []


def start(name, script, extra=None):
    config.ensure_dirs()
    log_path = os.path.join(config.LOG_DIR, name + ".log")
    log_file = open(log_path, "w", encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    cmd = [sys.executable, os.path.join("src", script)] + (extra or [])
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT, env=env)
    PROCS.append((name, proc, log_file, log_path))
    return proc


def stop_all(*_args):
    print("\n正在停止所有进程 ...")
    for name, proc, log_file, _path in PROCS:
        if proc.poll() is None:
            proc.terminate()
    deadline = time.time() + 5
    for name, proc, log_file, _path in PROCS:
        try:
            proc.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()
    print("已停止")


def main():
    if not os.path.isdir(config.DATA_DIR) or not os.listdir(config.DATA_DIR):
        subprocess.run([sys.executable, os.path.join("scripts", "make_objects.py")], cwd=ROOT, check=False)

    start("origin", os.path.join("origin", "origin.py"))
    for spec in config.EDGES:
        start("edge" + spec["id"], os.path.join("edge", "edge.py"), ["--id", spec["id"]])
    start("router", os.path.join("router", "dns_router.py"))
    time.sleep(1.5)

    print("拓扑已启动：")
    print("  源站      http://%s:%d" % (config.ORIGIN["host"], config.ORIGIN["port"]))
    for spec in config.EDGES:
        print("  边缘 %s    http://%s:%d   (/stats)" % (spec["id"], spec["bind"], spec["port"]))
    print("  DNS       udp %s:%d   (%s)"
          % (config.ROUTER["host"], config.ROUTER["port"], config.ROUTER["domain"]))
    print("  控制台    http://%s:%d/admin/edges" % (config.ROUTER["host"], config.ROUTER["admin_port"]))
    print("  日志      %s" % config.LOG_DIR)
    print("\n按 Ctrl+C 停止。建议另开一个终端运行 scripts/demo.py 或 scripts/experiments.py。")

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    try:
        while any(proc.poll() is None for _n, proc, _l, _p in PROCS):
            time.sleep(0.5)
    finally:
        stop_all()


if __name__ == "__main__":
    main()
