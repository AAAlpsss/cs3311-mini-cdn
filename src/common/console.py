"""控制台设置：Windows 终端默认 GBK，直接 print 中文日志会炸，这里统一切 UTF-8。"""
import sys


def setup():
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
