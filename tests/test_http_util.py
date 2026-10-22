import os
import socket
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common.http_util import (  # noqa: E402
    build_response, is_cacheable, parse_max_age, parse_range, read_request, read_response,
)


class TestCacheControl(unittest.TestCase):
    def test_max_age_parsed(self):
        self.assertEqual(parse_max_age("max-age=10", 5), 10)
        self.assertEqual(parse_max_age("public, max-age=3, must-revalidate", 5), 3)

    def test_max_age_default(self):
        self.assertEqual(parse_max_age(None, 5), 5)
        self.assertEqual(parse_max_age("no-cache", 5), 5)
        self.assertEqual(parse_max_age("max-age=abc", 5), 5)

    def test_cacheable(self):
        self.assertTrue(is_cacheable({"cache-control": "max-age=5"}))
        self.assertFalse(is_cacheable({"cache-control": "no-store"}))
        self.assertFalse(is_cacheable({"cache-control": "private, max-age=5"}))


class TestResponseBuilder(unittest.TestCase):
    def test_get_has_length_and_body(self):
        raw = build_response(200, {"Content-Type": "text/plain"}, b"hello")
        head, _, body = raw.partition(b"\r\n\r\n")
        self.assertEqual(body, b"hello")
        self.assertIn(b"Content-Length: 5", head)
        self.assertIn(b"HTTP/1.1 200 OK", head)

    def test_head_has_no_body(self):
        raw = build_response(200, {"Content-Type": "text/plain"}, b"hello", method="HEAD")
        self.assertNotIn(b"hello", raw)
        self.assertIn(b"Content-Length: 5", raw)

    def test_304_has_no_body_and_no_length(self):
        raw = build_response(304, {"ETag": '"abc"'}, b"ignored")
        self.assertNotIn(b"Content-Length", raw)
        self.assertIn(b"ETag", raw)


class TestRange(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(parse_range("bytes=0-9", 100), (0, 9))
        self.assertEqual(parse_range("bytes=90-", 100), (90, 99))

    def test_suffix(self):
        self.assertEqual(parse_range("bytes=-10", 100), (90, 99))

    def test_unsatisfiable(self):
        self.assertIsNone(parse_range("bytes=200-300", 100))
        self.assertIsNone(parse_range("bytes=50-10", 100))

    def test_multi_range_rejected(self):
        self.assertIsNone(parse_range("bytes=0-9,20-29", 100))

    def test_garbage(self):
        self.assertIsNone(parse_range("pages=1", 100))
        self.assertIsNone(parse_range(None, 100))


def socket_pair():
    return socket.socketpair()


class TestRequestParsing(unittest.TestCase):
    def _pair(self):
        return socket_pair()

    def test_basic_get(self):
        client, server = self._pair()
        try:
            client.sendall(b"GET /obj01.txt HTTP/1.1\r\nHost: a\r\nConnection: keep-alive\r\n\r\n")
            method, target, version, headers, body = read_request(server)
            self.assertEqual((method, target, version), ("GET", "/obj01.txt", "HTTP/1.1"))
            self.assertEqual(headers["host"], "a")
            self.assertEqual(body, b"")
        finally:
            client.close()
            server.close()

    def test_header_case_insensitive_and_body(self):
        client, server = self._pair()
        try:
            client.sendall(b"POST /x HTTP/1.1\r\nContent-Length: 4\r\nX-Test: 1\r\n\r\nbody")
            method, _target, _version, headers, body = read_request(server)
            self.assertEqual(method, "POST")
            self.assertEqual(headers["content-length"], "4")
            self.assertEqual(headers["x-test"], "1")
            self.assertEqual(body, b"body")
        finally:
            client.close()
            server.close()

    def test_split_packet(self):
        client, server = self._pair()
        try:
            client.sendall(b"GET /a HTTP/1.1\r\nHo")
            client.sendall(b"st: b\r\n\r\n")
            _method, _target, _version, headers, _body = read_request(server)
            self.assertEqual(headers["host"], "b")
        finally:
            client.close()
            server.close()

    def test_closed_connection_returns_none(self):
        client, server = self._pair()
        client.close()
        try:
            self.assertIsNone(read_request(server))
        finally:
            server.close()

    def test_bad_request_line_raises(self):
        client, server = self._pair()
        try:
            client.sendall(b"GARBAGE\r\n\r\n")
            with self.assertRaises(ValueError):
                read_request(server)
        finally:
            client.close()
            server.close()


class TestResponseParsing(unittest.TestCase):
    def _pair(self):
        return socket_pair()

    def test_304_no_body(self):
        client, server = self._pair()
        try:
            client.sendall(b"HTTP/1.1 304 Not Modified\r\nETag: \"abc\"\r\n\r\n")
            status, headers, body = read_response(server)
            self.assertEqual(status, 304)
            self.assertEqual(headers["etag"], '"abc"')
            self.assertEqual(body, b"")
        finally:
            client.close()
            server.close()

    def test_first_byte_callback(self):
        client, server = self._pair()
        calls = []
        try:
            client.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            status, _headers, body = read_response(server, on_first_byte=lambda: calls.append(1))
            self.assertEqual(status, 200)
            self.assertEqual(body, b"ok")
            self.assertEqual(len(calls), 1)
        finally:
            client.close()
            server.close()


if __name__ == "__main__":
    unittest.main()
