# 协议设计（Protocol Design）

本文是 mini-CDN 的协议契约。所有服务端实现都以此为准，改动协议必须先改本文件。

## 1. 角色与端口

| 角色 | 监听 | 职责 |
|---|---|---|
| Origin 源站 | `127.0.0.1:9000` | 持有对象原件，下发缓存元数据，支持条件请求与范围请求 |
| Edge A/B 边缘 | `127.0.0.1:8000` / `127.0.0.2:8000` | 缓存代理：命中直接回，未命中/过期回源 |
| Router 调度器 | `udp 127.0.0.1:5353` | DNS 应答 + 健康探测 + 就近/负载选路 |
| Router 控制台 | `127.0.0.1:8053` | 人/脚本读取状态、手动下线边缘 |
| Client 测量客户端 | — | 自写 DNS 查询 + 非持久 HTTP 请求，统计分阶段耗时 |

用 `127.0.0.1` 与 `127.0.0.2` 区分两台边缘，是为了让 DNS 的 A 记录能真正表达“不同边缘”，与真实 CDN 的 DNS 重定向一致。

## 2. HTTP 子集

### 2.1 请求

```
GET /obj05.txt HTTP/1.1\r\n
Host: www.cdn.test\r\n
Connection: keep-alive\r\n
If-None-Match: "b5254956abb23f07"\r\n
Range: bytes=0-1023\r\n
\r\n
```

- 方法：`GET`、`HEAD`（其余返回 `405`，带 `Allow: GET, HEAD`）
- 目标：`/` 开头的对象路径，可选查询串（`/admin/*` 使用）
- 版本：固定 `HTTP/1.1`
- 头部名大小写不敏感；解析时统一转小写
- 分帧：按 `Content-Length` 读取 body；**不支持** `chunked` 与请求流水线
- 头部上限 64 KiB，超限返回 `400`

### 2.2 响应

```
HTTP/1.1 200 OK\r\n
X-Edge: A\r\n
X-Cache: HIT\r\n
Age: 3\r\n
Content-Type: text/plain\r\n
ETag: "b5254956abb23f07"\r\n
Last-Modified: Wed, 21 Oct 2026 17:13:11 GMT\r\n
Cache-Control: max-age=5\r\n
Accept-Ranges: bytes\r\n
Content-Length: 16808\r\n
Date: ...\r\n
Server: sjtu-cn-cdn/0.1\r\n
Connection: keep-alive\r\n
\r\n
<16808 字节>
```

状态码集合：

| 码 | 语义 | 备注 |
|---|---|---|
| 200 | OK | 正常对象 |
| 206 | Partial Content | 带 `Content-Range: bytes a-b/total` |
| 304 | Not Modified | 条件请求命中；**无 body、无 Content-Length** |
| 400 | Bad Request | 请求行非法 / 头部超限 |
| 404 | Not Found | 对象不存在（源站） |
| 405 | Method Not Allowed | 非 GET/HEAD |
| 416 | Range Not Satisfiable | 多段或越界；带 `Content-Range: bytes */total` |
| 502 | Bad Gateway | 边缘回源失败 |

自定义观测头（冻结，不得改名）：

| 头 | 取值 | 含义 |
|---|---|---|
| `X-Cache` | `HIT` / `MISS` / `REVALIDATED` | 本次请求在边缘的处理结果 |
| `X-Edge` | `A` / `B` | 提供服务的边缘 |
| `Via` | `cdn-edge-A` | 代理链标识 |
| `Age` | 整数秒 | 副本已在边缘存活的时间；再验证后归零 |
| `X-Origin-Object` | 对象名 | 仅源站 200 响应携带 |

### 2.3 连接管理

- 默认 `Connection: keep-alive`，单连接可连续处理多个请求；客户端发 `Connection: close` 则处理完即关闭
- 服务端空闲超时 15 s；发送失败即断开
- 不做请求流水线（pipelining）：一次读一条请求，多余字节丢弃

### 2.4 缓存语义

| 源站响应头 | 边缘行为 |
|---|---|
| `Cache-Control: max-age=N`（N>0） | 存储，新鲜期 N 秒 |
| `Cache-Control: max-age=0` | 不入缓存 |
| `Cache-Control: no-store` / `private` | 不入缓存 |
| `ETag` / `Last-Modified` | 过期后用于带 `If-None-Match` / `If-Modified-Since` 再验证 |

边缘决策顺序：

1. 命中且 `now - stored_at < ttl` → `200` + `X-Cache: HIT`
2. 有副本但过期 → 带 `If-None-Match` 回源
   - 源站 `304` → 刷新 `stored_at`、按新 `max-age` 更新 ttl，回 `X-Cache: REVALIDATED`
   - 源站 `200` → 覆盖副本，回 `X-Cache: MISS`
3. 无副本 → 回源，`X-Cache: MISS`

`HEAD` 响应（无 body）**不写入缓存**，避免用空副本覆盖正常对象（见实现中的 `store(..., method)`）。

容量控制：按对象条目数计，超出后按 LRU 淘汰；淘汰计入 `evictions`。

## 3. DNS 子集

### 3.1 查询

标准 DNS 查询报文（UDP，最多 512 B），只处理 `IN` 类的 `A`（`QTYPE=1`），域名固定为 `www.cdn.test`。

```
Header: ID(2) FLAGS(2)=0x0100 QDCOUNT(2)=1 ANCOUNT(2)=0 NSCOUNT(2)=0 ARCOUNT(2)=0
Question: <labels> 0x00 QTYPE(2)=1 QCLASS(2)=1
```

### 3.2 应答

```
Header: 同一 ID, FLAGS=0x8180 (QR=1, RD=1, RA=1, RCODE=0), QDCOUNT=1, ANCOUNT=1
Question: 原样回填
Answer: NAME=0xC00C（指向问题中的域名，压缩指针）
        TYPE=1, CLASS=1, TTL=<配置，默认 5s>, RDLENGTH=4, RDATA=<边缘 IP>
```

其他域名或 `AAAA` 查询返回 `RCODE=3`（NXDOMAIN，`FLAGS=0x8183`），`ANCOUNT=0`。

TTL 取小值（5 s）的取舍：调度变化后客户端能较快感知（真实 CDN 常用 20–60 s），代价是解析请求更多。

### 3.3 选路算法

- 健康探测：每 2 s 对每个边缘 `GET /stats`，记录 RTT 与 `inflight`（在途请求数）
- 防抖动：连续 2 次探测失败才判为不可用；成功 1 次即恢复
- 评分：`score = RTT × (1 + 0.2 × inflight)`，取最小者；分数接近时轮转，避免单点过热
- 全部不可用时仍返回一个节点（客户端会得到 502），便于暴露故障而不是静默失败

## 4. 控制端点（只读观测 + 实验驱动）

| 端点 | 服务 | 作用 |
|---|---|---|
| `GET /health` | 全部 | 返回 `ok` |
| `GET /stats` | 边缘 | JSON：命中/未命中/再验证/淘汰/字节/在途/容量 |
| `GET /origin-stats` | 源站 | JSON：请求数、304 数、输出字节 |
| `GET /admin/clear` | 边缘 | 清空缓存 |
| `GET /admin/reset-stats` | 边缘、源站 | 统计清零（不清缓存） |
| `GET /admin/delay?ms=N` | 边缘、源站 | 注入响应时延，模拟链路延迟 |
| `GET /admin/capacity?n=N` | 边缘 | 改缓存容量 |
| `GET /admin/max-age?seconds=N` | 源站 | 改下发的 `max-age` |
| `GET /admin/edges` | 调度器 | 各边缘 RTT / 健康 / 被选次数 / 连续失败数 |
| `GET /admin/down?edge=A` | 调度器 | 手动下线（实验用） |
| `GET /admin/up?edge=A` | 调度器 | 恢复 |

`/stats` 字段固定为：
`requests, hits, misses, revalidated, evictions, bytes_from_origin, bytes_to_client, inflight, cache_entries, cache_capacity, hit_ratio`。

## 5. 已知限制

1. 不支持 `chunked` 传输编码、请求流水线、`gzip` 内容协商。
2. 缓存键只用路径，不处理 `Vary`（不区分 `Accept-Encoding` 等）。
3. 不做内容一致性校验（无 `If-Range`、无主动失效通知）；副本按 TTL 过期。
4. 只有一个域名、两类记录（`A` 与错误应答），不实现递归/迭代查询。
5. 单机演示用“响应注入时延”模拟传播时延，不是真实链路测量（见报告第 8 节讨论）。
