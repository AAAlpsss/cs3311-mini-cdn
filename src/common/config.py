"""全局配置：端口、路径、缓存与调度参数。

所有可调参数集中在这里，演示时只改本文件，不用翻代码。
注意：边缘节点 A/B 用 127.0.0.1 / 127.0.0.2 两个回环地址区分，
      这样 DNS 应答才能用「不同 IP」表示「不同边缘」，与真实 CDN 的
      DNS 重定向机制一致。
"""
import os

# .../application-layer-cdn/src/common/config.py -> 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "objects")
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results")
LOG_DIR = os.path.join(RESULTS_DIR, "logs")
RAW_DIR = os.path.join(RESULTS_DIR, "raw")

# 源站
ORIGIN = {
    "host": "127.0.0.1",
    "port": 9000,
    "max_age": 5,      # 下发的 Cache-Control: max-age（秒），调小便于观察 304
    # 人为响应时延（毫秒）：模拟「源站离客户端很远」，只在回源链路上产生等待。
    # 单机演示时全程 0，做 E1/E5 时用 50ms 才能看出 CDN 的收益。
    "delay_ms": 0,
}

# 边缘缓存节点
EDGES = [
    {"id": "A", "bind": "127.0.0.1", "port": 8000, "capacity": 5, "delay_ms": 0},
    {"id": "B", "bind": "127.0.0.2", "port": 8000, "capacity": 5, "delay_ms": 0},
]
EDGE_PORT = EDGES[0]["port"]          # 客户端访问边缘用的端口

# DNS 调度器
ROUTER = {
    "host": "127.0.0.1",
    "port": 5353,                     # DNS 服务端口
    "admin_port": 8053,               # 控制/观测用的 HTTP 端口
    "domain": "www.cdn.test",         # 被调度的域名（.test 保留域，不会真去公网）
    "ttl": 5,                         # DNS 应答 TTL（秒）
    "probe_interval": 2.0,            # 健康探测周期（秒）
    "probe_timeout": 1.0,             # 单次探测超时（秒）
    "load_weight": 0.2,               # 活跃连接数的权重（越大越「按负载」）
}


def ensure_dirs():
    for d in (DATA_DIR, RESULTS_DIR, LOG_DIR, RAW_DIR):
        os.makedirs(d, exist_ok=True)
