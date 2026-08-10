"""Release guard for explanatory documentation on every named function."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_all_named_functions_have_explanatory_documentation() -> None:
    """Запустить the same documentation gate used by release QA and require success."""

    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "scripts/check_function_docs.py"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "failures=0" in completed.stdout
    assert "Python=" in completed.stdout
    assert "JavaScript named=" in completed.stdout
