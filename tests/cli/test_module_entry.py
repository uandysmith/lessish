from __future__ import annotations

import io
import runpy
import sys
import unittest
from contextlib import redirect_stdout


class TestModuleEntryPoint(unittest.TestCase):
    def test_python_m_lessish_version(self) -> None:
        saved_argv = sys.argv
        sys.argv = ['lessish', 'version']
        out = io.StringIO()
        try:
            with redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
                runpy.run_module('lessish', run_name='__main__')
        finally:
            sys.argv = saved_argv
        self.assertEqual(ctx.exception.code, 0)
        self.assertTrue(out.getvalue().startswith('lessish '))


if __name__ == '__main__':
    unittest.main()
