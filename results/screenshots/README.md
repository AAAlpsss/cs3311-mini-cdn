# 截图清单（提交前补齐）

`report.md` 里提到的截图放这里，命名如下：

| 文件名 | 内容 | 怎么截 |
|---|---|---|
| `canvas-group.png` | Canvas 大作业分组页（证明 3 人一组） | 浏览器截图 |
| `curl-hit.png` | 连续两次 `curl -i http://127.0.0.1:8000/obj05.txt`，第二次出现 `X-Cache: HIT` | 终端截图 |
| `wireshark-200.png` | 一次完整 GET 的请求/响应（含 `Content-Length`） | Wireshark 过滤 `http` |
| `wireshark-304.png` | 边缘回源带 `If-None-Match`，源站回 `304 Not Modified` 且无 body | Wireshark 过滤 `http.response.code == 304` |
| `router-edges.png` | `http://127.0.0.1:8053/admin/edges` 的 JSON（RTT/健康/被选次数） | 浏览器截图 |
| `failover.png` | `down?edge=A` 前后两次 `client.py --resolve-only` 的输出 | 终端截图 |
| `stats.png` | 边缘 `/stats` 与源站 `/origin-stats` 的终态 | 浏览器/终端截图 |
| `submitted.png` | Canvas 提交成功页 | 浏览器截图 |

Wireshark 抓回环需要 Npcap 的 Loopback 适配器；没有的话用 WSL 里 `tcpdump -i lo`，或退而用 `curl -v` 输出代替（报告里说明即可）。
