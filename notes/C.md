# C 的工作日志（调度与数据）

## 我的文件（只有我能改）

- `src/router/dns_router.py` — DNS 调度器
- `src/client/client.py` — 测量客户端
- `scripts/experiments.py` — 实验脚本
- `scripts/plots.py` — 出图脚本（待建）
- 报告：3.2 DNS 协议 / 5 调度策略 / 7 实验结果

## 我的任务

| ID | 任务 | 截止 | 状态 |
|---|---|---|---|
| T05 | 复核 `client.py`（TTFB 口径、CSV 字段、zipf） | 10-22 12:00 | TODO |
| T08 | 调度器防抖（连续 2 次失败才判死）+ `selections` 统计 | 10-22 15:30 | TODO |
| T09 | 修 `experiments.py` 的 E3/E5；写 `plots.py` | 10-22 18:00 | TODO |
| T12 | 终版图 5–7 张（统一单位与图号） | 10-23 11:30 | TODO |
| T15 | 报告 3.2/5/7 | 10-23 16:00 | TODO |
| T17 | 打包 + 干净目录解压自测 | 10-23 17:00 | TODO |

## 自检命令（改完必跑）

```bash
python -m compileall src
python scripts/run_all.py                          # 终端 1
python src/client/client.py --resolve-only         # 应输出 www.cdn.test -> 127.0.0.1 或 .2
curl http://127.0.0.1:8053/admin/edges
curl "http://127.0.0.1:8053/admin/down?edge=A"     # 再解析应切到 B
python scripts/experiments.py e4
```

**验收基线**：down 掉被选中的边缘后，`client.py --resolve-only` 返回另一个 IP，且请求仍 200。

## AI 用法

- DNS 报文：让 AI 逐字段解释你构造的 `struct.pack(">HHHHHH", ...)` 与压缩指针 `\xc0\x0c`，你要能口述。
- 图：贴 CSV 表头，要求“只输出 matplotlib 脚本，读 `results/raw/*.csv`，输出 pdf+png，中文字体回退”。
- 结论：让 AI 列出“这组数据能支持/不能支持哪些结论”，避免过度解读（评阅常抓这个）。

## 日志

| 日期 | 完成 | 阻塞 | 次日 | 工时 |
|---|---|---|---|---|
| 10-22 | | | | |
| 10-23 | | | | |
