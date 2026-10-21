"""生成测试对象（不同大小，方便做时延/吞吐实验）。

运行： python scripts/make_objects.py
产物： data/objects/obj01.txt ... obj10.txt（1 KB ~ 1 MB）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common import config, console  # noqa: E402

console.setup()

# (文件名, 大小 KB)
OBJECTS = [
    ("obj01.txt", 1), ("obj02.txt", 2), ("obj03.txt", 4), ("obj04.txt", 8),
    ("obj05.txt", 16), ("obj06.txt", 32), ("obj07.txt", 64), ("obj08.txt", 128),
    ("obj09.txt", 512), ("obj10.txt", 1024),
]


def main():
    config.ensure_dirs()
    line = "SJTU CS3311 mini-CDN test object. "
    for name, kb in OBJECTS:
        path = os.path.join(config.DATA_DIR, name)
        target = kb * 1024
        with open(path, "w", encoding="utf-8") as fh:
            written = 0
            while written < target:
                line_no = "%s #%06d\n" % (line, written)
                fh.write(line_no)
                written += len(line_no.encode("utf-8"))
        print("生成 %-10s %6d KB" % (name, kb))
    print("对象目录：%s" % config.DATA_DIR)


if __name__ == "__main__":
    main()
