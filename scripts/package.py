"""打包提交物：只用白名单，避免把缓存、日志、测试对象塞进去。

运行： python scripts/package.py
产物： submission.zip（项目根目录）
"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common import config, console  # noqa: E402

console.setup()

# 白名单：目录/文件 -> 递归
INCLUDE_DIRS = ["src", "scripts", "tests", "results"]
INCLUDE_FILES = ["report.md", "report.pdf", "README.md", "docs/protocol.md"]
EXCLUDE_DIRS = {"__pycache__", ".git", ".venv", "logs", "objects"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log")


def should_include(path):
    parts = set(path.replace("\\", "/").split("/"))
    if parts & EXCLUDE_DIRS:
        return False
    if path.endswith(EXCLUDE_SUFFIX):
        return False
    return True


def main():
    out_path = os.path.join(ROOT, "submission.zip")
    if os.path.exists(out_path):
        os.remove(out_path)

    added = []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in INCLUDE_FILES:
            full = os.path.join(ROOT, name)
            if os.path.isfile(full):
                zf.write(full, name)
                added.append(name)

        for top in INCLUDE_DIRS:
            for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, top)):
                dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
                for filename in filenames:
                    full = os.path.join(dirpath, filename)
                    rel = os.path.relpath(full, ROOT).replace("\\", "/")
                    if not should_include(rel):
                        continue
                    zf.write(full, rel)
                    added.append(rel)

    size_kb = os.path.getsize(out_path) / 1024
    print("已生成 %s（%d 个文件，%.1f KB）" % (os.path.basename(out_path), len(added), size_kb))
    for rel in sorted(added):
        print("  " + rel)


if __name__ == "__main__":
    main()
