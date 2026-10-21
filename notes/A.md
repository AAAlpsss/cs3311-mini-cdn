# A 的工作日志（协议与源站）

## 我的文件（只有我能改）

- `src/common/http_util.py` — HTTP 报文解析/构造
- `src/common/http_client.py` — 回源与探测用的极简客户端
- `src/origin/origin.py` — 源站
- `tests/test_http_util.py` — 我的单测
- 报告：1 简介 / 2 架构 / 3.1 HTTP 协议设计

## 我的任务

| ID | 任务 | 截止 | 状态 |
|---|---|---|---|
| T02 | `docs/protocol.md`（HTTP/DNS 子集 + 报文示例） | 10-22 12:00 | TODO |
| T03 | `http_util` 加固 + 10 条单测 | 10-22 12:00 | TODO |
| T06 | 源站 Range / If-Modified-Since / 错误码 | 10-22 15:30 | TODO |
| T10 | E1/E2 数据 + Wireshark 抓 200/304 | 10-22 18:00 | TODO |
| T13 | 报告 1/2/3.1 | 10-22 21:30 | TODO |
| T16 | 合并/统稿/PDF | 10-23 17:00 | TODO |

## 自检命令（改完必跑）

```bash
python -m compileall src
python -m unittest tests.test_http_util -v
python scripts/run_all.py          # 终端 1
curl -i http://127.0.0.1:9000/obj05.txt
curl -i -H "If-None-Match: <上一步的 ETag>" http://127.0.0.1:9000/obj05.txt   # 应 304
curl -i -r 0-99 http://127.0.0.1:9000/obj05.txt                                # 应 206
```

**验收基线**：E1 里直连源站 TTFB ≈ 51 ms（源站 50 ms 时延），边缘 HIT ≈ 0.5 ms。

## AI 用法

- 写单测：贴 `http_util.py`，要求“只输出 unittest 用例，覆盖超长头/无 body/半包/关闭连接，不改被测文件”。
- 抓包前：让 AI 解释“为什么 304 没有 Content-Length 也能被正确解析”，再自己验证。
- 报告排版：只让 AI 润色，数字必须来自 `results/raw/`。

## 日志

| 日期 | 完成 | 阻塞 | 次日 | 工时 |
|---|---|---|---|---|
| 10-22 | | | | |
| 10-23 | | | | |
