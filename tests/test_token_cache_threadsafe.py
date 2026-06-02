"""The value-token cache must not race across threads.

`parse_value_text` memoises tokenisation in an LRU dict whose
get/move_to_end/setitem/popitem sequence is not atomic. The cache is
therefore thread-local: each thread gets its own dict, so concurrent
compiles (the documented "two threads sharing one instance" scenario)
never touch the same OrderedDict. These tests exercise that isolation
and prove output stays correct under contention.
"""

from __future__ import annotations

import threading
import unittest

from lessish import Lessish
from lessish.parser import values as _values


class TestTokenCacheThreadSafe(unittest.TestCase):
    def test_concurrent_compiles_are_correct(self) -> None:
        ls = Lessish()
        src_a = '@c: red;' + ''.join(
            f'.r{i}{{color:lighten(@c,{i % 40}%);margin:({i} * 2px);width:calc(100% - {i}px)}}' for i in range(80)
        )
        src_b = '@d: blue;' + ''.join(f'.q{i}{{border:{i}px solid @d;padding:{i}px}}' for i in range(80))
        expected_a = ls.compile(src_a)
        expected_b = ls.compile(src_b)

        errors: list[str] = []

        def work(use_a: bool) -> None:
            src, expected = (src_a, expected_a) if use_a else (src_b, expected_b)
            for _ in range(12):
                try:
                    if ls.compile(src) != expected:
                        errors.append('output mismatch under contention')
                except Exception as exc:  # noqa: BLE001 - surface any race
                    errors.append(repr(exc))

        threads = [threading.Thread(target=work, args=(i % 2 == 0,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])

    def test_cache_is_thread_local(self) -> None:
        # Each thread sees its own backing dict — never the main thread's.
        main_cache = _values._thread_cache()
        seen: list[int] = []

        def grab() -> None:
            seen.append(id(_values._thread_cache()))

        t = threading.Thread(target=grab)
        t.start()
        t.join()

        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], id(main_cache))

    def test_cache_bounded_per_thread(self) -> None:
        # The LRU cap holds within a single thread.
        cache = _values._thread_cache()
        cache.clear()
        ls = Lessish()
        ls.compile('@x: 0;' + ''.join(f'.c{i}{{a{i}:{i}px}}' for i in range(_values._TOKEN_CACHE_MAX + 500)))
        self.assertLessEqual(len(_values._thread_cache()), _values._TOKEN_CACHE_MAX)


if __name__ == '__main__':
    unittest.main()
