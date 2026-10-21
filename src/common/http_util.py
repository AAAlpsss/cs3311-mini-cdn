"""HTTP/1.1 报文的解析与构造（手写，不依赖 http.server / requests）。

实现范围（够本项目用）：
  · 请求行 method target HTTP/1.1
  · 头部 key: value（大小写不敏感）
  · body 按 Content-Length 读取
  · 响应状态行 + 头部 + body
不支持 chunked 传输编码（我们自己的服务端始终发 Content-Length）。
"""
from email.utils import formatdate

REASONS = {
    200: "OK",
    206: "Partial Content",
    304: "Not Modified",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
    502: "Bad Gateway",
    504: "Gateway Timeout",
}

MAX_HEADER_BYTES = 64 * 1024


def read_request(conn):
    """从一个连接读取一条请求，返回 (method, target, version, headers, body)。

    连接被对端关闭时返回 None；报文非法时抛 ValueError。
    """
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
        if len(buf) > MAX_HEADER_BYTES:
            raise ValueError("request header too large")

    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    if not lines or not lines[0].strip():
        return None

    try:
        parts = lines[0].decode("latin-1").split(" ")
        method, target, version = parts[0], parts[1], parts[2]
    except (IndexError, UnicodeDecodeError):
        raise ValueError("bad request line")

    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.decode("latin-1").partition(":")
        if not sep:
            continue
        headers[name.strip().lower()] = value.strip()

    length = parse_content_length(headers)
    body = rest
    while len(body) < length:
        chunk = conn.recv(65536)
        if not chunk:
            break
        body += chunk

    return method.upper(), target, version, headers, body[:length]


def parse_content_length(headers):
    try:
        return max(0, int(headers.get("content-length") or 0))
    except ValueError:
        return 0


def build_response(status, headers, body=b"", method="GET", keep_alive=True):
    """构造完整响应字节流。304/204 不写 body 也不写 Content-Length。"""
    headers = dict(headers or {})
    headers.setdefault("Server", "sjtu-cn-cdn/0.1")
    headers["Date"] = formatdate(usegmt=True)
    headers["Connection"] = "keep-alive" if keep_alive else "close"

    bodyless = status in (204, 304) or method == "HEAD"
    if not bodyless:
        headers["Content-Length"] = str(len(body))
    elif status not in (204, 304):
        headers["Content-Length"] = str(len(body))

    lines = ["HTTP/1.1 %d %s" % (status, REASONS.get(status, "OK"))]
    lines += ["%s: %s" % (k, v) for k, v in headers.items()]
    head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
    return head if bodyless else head + body


def read_response(sock):
    """读取一条响应，返回 (status, headers, body)。供边缘/客户端回源使用。"""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("connection closed before response headers")
        buf += chunk

    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    try:
        _, status_text, _version = lines[0].decode("latin-1").split(" ", 2)
    except ValueError:
        raise ConnectionError("bad status line")

    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.decode("latin-1").partition(":")
        if not sep:
            continue
        headers[name.strip().lower()] = value.strip()

    status = int(status_text)
    length = parse_content_length(headers)
    body = rest
    while len(body) < length:
        chunk = sock.recv(65536)
        if not chunk:
            break
        body += chunk
    return status, headers, body[:length]


def parse_max_age(cache_control, default):
    """从 Cache-Control 里取 max-age；没写返回 default。"""
    if not cache_control:
        return default
    for part in cache_control.split(","):
        part = part.strip().lower()
        if part.startswith("max-age="):
            try:
                return max(0, int(part.split("=", 1)[1]))
            except ValueError:
                return default
    return default


def is_cacheable(headers):
    """no-store / private 之外都当作可缓存。"""
    cc = (headers.get("cache-control") or "").lower()
    return "no-store" not in cc and "private" not in cc
