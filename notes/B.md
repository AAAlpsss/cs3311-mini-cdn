# B 的工作日志（边缘缓存）

## 我的文件（只有我能改）

- `src/edge/edge.py` — 边缘缓存代理
- `tests/test_cache.py` — LRU/TTL 纯逻辑单测
- `README.md` 的部署与排错部分
- 报告：4 缓存策略 / 6 部署与运行

## 我的任务

| ID | 任务 | 截止 | 状态 |
|---|---|---|---|
| T04 | 缓存纯逻辑单测（LRU / TTL=0 / 容量 1） | 10-22 12:00 | TODO |
| T07 | 再验证与 Age 重置、HEAD、no-store、线程安全审计 | 10-22 15:30 | TODO |
| T11 | E3 数据 + E5 按新设计重跑（3 次中位） | 10-22 18:00 | TODO |
| T14 | 报告第 4 节 + 第 6 节部署实测 | 10-23 16:00 | TODO |
| T18 | 贡献度表 + 桌面存档同步 | 10-23 17:00 | TODO |

## E5 必须改设计（重要）

现在的 `experiments.py e5` 是「10 个对象顺序打 30 次」，大部分是 MISS，多了一跳，所以边缘吞吐低于直连源站——**这是正确的现象，但不是要证明的结论**。改成：

1. 先把 3 个热点对象各请求一次（预热）；
2. 再对 3 个热点对象重复请求 33 次（共 99 次）；
3. 同样的请求序列分别打「直连源站（`/admin/delay?ms=50`）」与「经边缘」；
4. 报告里说明为什么这么设计（“CDN 的收益来自命中，而不是首次请求”）。

## 自检命令（改完必跑）

```bash
python -m compileall src
python -m unittest tests.test_cache -v
python scripts/run_all.py                 # 终端 1
curl -i http://127.0.0.1:8000/obj05.txt   # 第一次 MISS
curl -i http://127.0.0.1:8000/obj05.txt   # 第二次 HIT，Age 增长
python scripts/experiments.py e3
```

**验收基线**：TTL 过期后再请求 → `X-Cache: REVALIDATED`，源站 `not_modified` +1。

## AI 用法

- 锁审计：把 `edge.py` 贴给 AI，问“哪些共享状态没有被锁保护？`inflight` 在异常路径会泄漏吗？”
- 单测：要求“纯逻辑、不起 socket、不依赖时间（可注入 now）”。
- 报告第 4 节：让 AI 只做结构梳理，权衡结论（新鲜度 vs 命中率）必须自己写。

## 日志

| 日期 | 完成 | 阻塞 | 次日 | 工时 |
|---|---|---|---|---|
| 10-22 | | | | |
| 10-23 | | | | |
