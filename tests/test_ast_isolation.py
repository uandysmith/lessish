"""`evaluate(copy_input=True)` keeps a reused parse tree pristine.

Evaluation memoises parsed values, splices imports, and stamps closure
frames onto AST nodes. Those writes must not leak back onto a tree the
caller intends to reuse across compiles (or share between threads). The
default `copy_input=True` clones the input first; `copy_input=False`
(what `compile()` uses internally) opts out for single-use trees.
"""

from __future__ import annotations

import threading
import unittest

from lessish import Lessish, Source

_CACHE_FIELDS = ('_parsed_ast', '_parsed_ast_body', '_parsed_has_dr', '_parsed_is_static', '_evaled_node')
_FRAME_FIELDS = ('_var_index_cache', '_mixin_index_cache')


def _populated_caches(node: object, acc: list[str] | None = None) -> list[str]:
    """Names of per-compile cache fields that got populated anywhere in
    the tree rooted at `node`."""
    if acc is None:
        acc = []
    for f in (*_CACHE_FIELDS, *_FRAME_FIELDS):
        if getattr(node, f, None) is not None:
            acc.append(f)
    for attr in getattr(type(node), '__lessish_child_attrs__', ()):
        child = getattr(node, attr, None)
        if isinstance(child, list):
            for c in child:
                if hasattr(c, 'index'):
                    _populated_caches(c, acc)
        elif hasattr(child, 'index'):
            _populated_caches(child, acc)
    return acc


_SRC = '.mix() { color: red; } @x: { c: @v; } .a { @v: 1; .mix(); width: (1 + 2); @x(); }'


class CopyInputTests(unittest.TestCase):
    def test_default_leaves_caller_ast_pristine(self) -> None:
        ls = Lessish()
        ast = ls.parse(_SRC)
        ls.evaluate(ast, src=Source(_SRC))  # copy_input defaults True
        self.assertEqual(_populated_caches(ast), [])

    def test_copy_input_false_annotates_in_place(self) -> None:
        ls = Lessish()
        ast = ls.parse(_SRC)
        ls.evaluate(ast, src=Source(_SRC), copy_input=False)
        self.assertNotEqual(_populated_caches(ast), [])

    def test_reused_tree_gives_identical_output(self) -> None:
        ls = Lessish()
        ast = ls.parse(_SRC)
        out1 = ls.emit(ls.evaluate(ast, src=Source(_SRC)), src=Source(_SRC))
        out2 = ls.emit(ls.evaluate(ast, src=Source(_SRC)), src=Source(_SRC))
        self.assertEqual(out1, out2)
        self.assertIn('color: red', out1)
        self.assertIn('width: 3', out1)

    def test_shared_tree_across_threads_is_consistent(self) -> None:
        ls = Lessish()
        ast = ls.parse(_SRC)
        results: list[str] = []
        lock = threading.Lock()

        def work() -> None:
            for _ in range(40):
                css = ls.emit(ls.evaluate(ast, src=Source(_SRC)), src=Source(_SRC))
                with lock:
                    results.append(css)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(results)), 1)
        # And the caller's tree is still clean after concurrent reuse.
        self.assertEqual(_populated_caches(ast), [])


class CloneTests(unittest.TestCase):
    """`Node.__deepcopy__` must clone field-wise: equal to the original,
    structural lists isolated, but the immutable `Source` shared (not
    re-cloned). Guards the clone against a new field that should be
    deep-copied being accidentally shared, or vice versa."""

    def test_clone_equal_but_lists_isolated(self) -> None:
        from copy import deepcopy

        ast = Lessish().parse(_SRC)
        clone = deepcopy(ast)
        self.assertEqual(clone, ast)
        self.assertIsNot(clone, ast)
        self.assertIsNot(clone.rules, ast.rules)

    def test_clone_shares_immutable_source(self) -> None:
        from copy import deepcopy

        src = Source(_SRC)
        ast = Lessish().parse(src)
        ast._source = src
        ast.rules[0]._source = src
        clone = deepcopy(ast)
        self.assertIs(clone._source, src)
        self.assertIs(clone.rules[0]._source, src)


if __name__ == '__main__':
    unittest.main()
