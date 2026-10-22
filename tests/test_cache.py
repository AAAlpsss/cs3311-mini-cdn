import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def load_edge_module():
    path = os.path.join(ROOT, "src", "edge", "edge.py")
    spec = importlib.util.spec_from_file_location("edge_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


edge = load_edge_module()
CacheEntry = edge.CacheEntry
LRUCache = edge.LRUCache


class TestLRUCache(unittest.TestCase):
    def test_evicts_least_recently_used(self):
        cache = LRUCache(3)
        for name in ("a", "b", "c"):
            cache.put(name, CacheEntry(b"x", None, None, 60, "text/plain"))
        cache.get("a")                      # a 变成最近使用
        cache.put("d", CacheEntry(b"x", None, None, 60, "text/plain"))
        self.assertEqual(sorted(cache.keys()), ["a", "c", "d"])
        self.assertIsNone(cache.get("b"))

    def test_capacity_one(self):
        cache = LRUCache(1)
        cache.put("a", CacheEntry(b"1", None, None, 60, "text/plain"))
        cache.put("b", CacheEntry(b"2", None, None, 60, "text/plain"))
        self.assertEqual(cache.keys(), ["b"])

    def test_overwrite_updates_entry(self):
        cache = LRUCache(2)
        cache.put("a", CacheEntry(b"old", None, None, 60, "text/plain"))
        cache.put("a", CacheEntry(b"new", None, None, 60, "text/plain"))
        self.assertEqual(cache.get("a").body, b"new")
        self.assertEqual(len(cache.keys()), 1)

    def test_clear(self):
        cache = LRUCache(2)
        cache.put("a", CacheEntry(b"1", None, None, 60, "text/plain"))
        self.assertEqual(cache.clear(), 1)
        self.assertEqual(cache.keys(), [])


class TestEntryFreshness(unittest.TestCase):
    def test_zero_ttl_never_fresh(self):
        entry = CacheEntry(b"x", None, None, 0, "text/plain")
        self.assertFalse(entry.is_fresh())

    def test_fresh_within_ttl_and_stale_after(self):
        entry = CacheEntry(b"x", None, None, 60, "text/plain")
        self.assertTrue(entry.is_fresh(entry.stored_at + 59))
        self.assertFalse(entry.is_fresh(entry.stored_at + 61))

    def test_age(self):
        entry = CacheEntry(b"x", None, None, 60, "text/plain")
        self.assertEqual(entry.age(entry.stored_at + 3.7), 3)


class TestStorePolicy(unittest.TestCase):
    """HEAD 不能入缓存（否则会用空 body 覆盖正常副本），no-store 也不能。"""

    def setUp(self):
        edge.CACHE.clear()
        edge.CACHE.capacity = 5

    def _handler(self):
        return object.__new__(edge.Handler)     # 只调 store()，不需要真的 socket

    def test_head_not_cached(self):
        self._handler().store("/obj01.txt", {"cache-control": "max-age=60", "etag": '"x"'}, b"", "HEAD")
        self.assertEqual(edge.CACHE.keys(), [])

    def test_no_store_not_cached(self):
        self._handler().store("/obj01.txt", {"cache-control": "no-store", "etag": '"x"'}, b"data", "GET")
        self.assertEqual(edge.CACHE.keys(), [])

    def test_max_age_zero_not_cached(self):
        self._handler().store("/obj01.txt", {"cache-control": "max-age=0", "etag": '"x"'}, b"data", "GET")
        self.assertEqual(edge.CACHE.keys(), [])

    def test_normal_get_cached(self):
        self._handler().store("/obj01.txt", {"cache-control": "max-age=30", "etag": '"x"',
                                             "content-type": "text/plain"}, b"data", "GET")
        entry = edge.CACHE.get("/obj01.txt")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.ttl, 30)
        self.assertEqual(entry.body, b"data")


if __name__ == "__main__":
    unittest.main()
