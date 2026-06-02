"""Guard: every example in the documentation must still work.

This runs the documentation checker (``docs/check_examples.py``) as a
subprocess — exactly the way it's documented to be invoked — so an API change
that breaks a documented example fails CI rather than rotting in the docs.
See the checker's module docstring for the example conventions.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

_CHECKER = Path(__file__).resolve().parent.parent / 'docs' / 'check_examples.py'


class TestDocumentationExamples(unittest.TestCase):
    def test_checker_is_present(self) -> None:
        self.assertTrue(_CHECKER.exists(), 'docs/check_examples.py is missing')

    def test_all_examples_pass(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(_CHECKER)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode,
            0,
            'documentation examples are out of date — run '
            f'`python docs/check_examples.py`:\n{proc.stdout}\n{proc.stderr}',
        )


if __name__ == '__main__':
    unittest.main()
