# mini-CDN：应用层 CDN 原型（CS3311 大作业）

用 Python 标准库手写 HTTP/1.1 与 DNS 报文，实现一个可演示、可测量的迷你 CDN：
**源站 + 两个边缘缓存 + DNS 智能调度器**，并附带 5 组对照实验。

- 协议细节：`docs/protocol.md`
- 三人分工与排期：`docs/三人任务总规划.md`
- 实验报告：`report.md`

## 快速开始

```bash
# 0. 确认 Python（3.8+，仅标准库；出图需要 matplotlib）
python --version

# 1. 生成测试对象（data/objects/*.txt，1 KB ~ 1 MB）
python scripts/make_objects.py

# 2. 启动全拓扑（终端 1）
python scripts/run_all.py

# 3. 现场演示（终端 2）：MISS -> HIT -> REVALIDATED -> DNS 选路 -> 故障切换
python scripts/demo.py

# 4. 跑实验并落 CSV（终端 2）
python scripts/experiments.py all
python scripts/plots.py          # 出图到 results/figures/
```

`run_all.py` 在前台合并日志；按 `Ctrl+C` 会一并收掉子进程。想看单进程日志：`results/logs/*.log`。

## 端口

| 服务 | 地址 |
|---|---|
| 源站 | `http://127.0.0.1:9000` |
| 边缘 A | `http://127.0.0.1:8000` |
| 边缘 B | `http://127.0.0.2:8000` |
| DNS | `udp 127.0.0.1:5353`（域名 `www.cdn.test`） |
| 调度控制台 | `http://127.0.0.1:8053/admin/edges` |

## 手动验证（不用脚本）

```bash
curl -i http://127.0.0.1:8000/obj05.txt          # 第一次：X-Cache: MISS
curl -i http://127.0.0.1:8000/obj05.txt          # 第二次：X-Cache: HIT，Age 增长
sleep 6 && curl -i http://127.0.0.1:8000/obj05.txt   # 过期后：X-Cache: REVALIDATED

curl -i -r 0-99 http://127.0.0.1:9000/obj05.txt  # 206 Partial Content
curl -i -H 'If-None-Match: "<ETag>"' http://127.0.0.1:9000/obj05.txt   # 304

python src/client/client.py --resolve-only       # DNS 解析到哪个边缘
curl "http://127.0.0.1:8053/admin/down?edge=A"   # 下线 A
python src/client/client.py --resolve-only       # 应切到 B
curl "http://127.0.0.1:8053/admin/up?edge=A"
```

## 目录

```
src/common/    config / http_util（报文解析构造）/ http_client（回源）/ console
src/origin/    源站：200/304/206、max-age、delay 注入、/origin-stats
src/edge/      边缘：LRU+TTL 缓存、条件请求再验证、/stats、/admin/*
src/router/    调度：手写 DNS A 记录应答、健康探测、故障切换、/admin/edges
src/client/    测量客户端：自写 DNS 查询 + 分阶段耗时、Zipf 请求分布
scripts/       make_objects / run_all / demo / experiments / plots
tests/         test_http_util（报文与 Range）、test_cache（LRU/TTL/入缓存策略）
data/objects/  生成的测试对象（不进仓库）
results/       raw（CSV）/ figures（图）/ logs / screenshots
```

## 测试

```bash
python -m unittest discover -s tests -v      # 29 条断言
```

## 实验一览

| 编号 | 内容 | 驱动 |
|---|---|---|
| E1 | 无 CDN vs 边缘 MISS vs 边缘 HIT 的首字节时间 | `experiments.py e1` |
| E2 | 条件请求再验证：304 不传 body | `experiments.py e2` |
| E3 | LRU 淘汰过程 + TTL 与命中率的权衡 | `experiments.py e3` |
| E4 | DNS 就近调度与边缘故障切换 | `experiments.py e4` |
| E5 | 预热后热点内容的聚合吞吐与并发 | `experiments.py e5` |

数据落 `results/raw/*.csv`，图落 `results/figures/`，报告第 7 节引用。

## 排错

| 现象 | 原因 / 处理 |
|---|---|
| `python` 打开应用商店 | 用真实解释器，如 `C:\Users\<你>\anaconda3\python.exe`，或在 conda prompt 里跑 |
| `OSError: [WinError 10048]` 端口被占 | 上次没退干净：`netstat -ano \| findstr :8000` 后 `taskkill /PID <pid> /F` |
| 边缘 B 起不来 | 需要系统支持绑定 `127.0.0.2`（Windows 10/11 默认支持）；不支持就把 `src/common/config.py` 里 B 的 `bind` 改成 `127.0.0.1` 并改端口 |
| 解析总落在同一台 | 两台同在一台机器时 RTT 接近，属正常；用 `/admin/down` 演示切换 |
| 图里中文变方框 | 缺 `Microsoft YaHei`/`SimHei`；`scripts/plots.py` 顶部字体列表里换成本机存在的字体 |
| 实验数字与报告不一致 | 先 `experiments.py all` 再 `plots.py`；报告里所有数字都能在 `results/raw/*.csv` 查到 |
| 单机看不到延迟差异 | 回环没有链路时延，实验脚本会自动给源站 `/admin/delay?ms=50`；手工验证时自行加 |

## 设计要点（报告里展开）

1. **命中不跨越“用户—源站”链路**：这是 CDN 收益的来源，也是 E1 里 HIT 比直连快两个数量级的原因。
2. **再验证只传响应头**：过期不等于失效，`304` 让边缘复用副本，省的是带宽而不是时延。
3. **TTL 是权衡**：max-age 小于请求间隔时命中率塌到 0（E3 实测 0% → 75%）。
4. **调度要有防抖与失败兜底**：单次探测失败就切换会引起抖动；连续失败才判死，且不静默失败。
