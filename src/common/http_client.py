"""极简 HTTP 客户端（回源、健康探测、控制台调用都用它）。

每次调用新建连接——边缘回源本来就是「按需拉取」，这样也更贴近
真实 CDN 的 cache-miss 行为（顺便省掉连接池的复杂度）。
"""
import socket

from .http_util import build_response, read_response  # noqa: F401  (build_response 便于快速自测)


def request(host, port, path, method="GET", headers=None, timeout=3.0, body=b""):
    """返回 (status, headers, body)。连接失败时抛 OSError。"""
    hdrs = {"Host": "%s:%d" % (host, port), "Connection": "close", "Accept": "*/*"}
    hdrs.update(headers or {})
    if body:
        hdrs["Content-Length"] = str(len(body))

    lines = ["%s %s HTTP/1.1" % (method, path)] + ["%s: %s" % (k, v) for k, v in hdrs.items()]
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body

    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(raw)
        return read_response(sock)
