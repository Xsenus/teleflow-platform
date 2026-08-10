from __future__ import annotations

"""Pytest entry point with deterministic post-session termination.

Some managed CI/container runtimes preload telemetry plugins or libraries that
keep non-daemon helper threads alive after pytest has already completed.  A
plain ``pytest.main()`` may therefore print a successful summary but never
return to the caller.

The plugin below exits from the *last* ``pytest_sessionfinish`` hook. At that
point pytest's terminal reporter has flushed the functional result, while
unrelated interpreter-level ``atexit`` handlers are intentionally skipped.
Coverage is collected by the separate ``run_pytest_coverage.py`` entry point.
"""

import os
import sys

os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")

import pytest


class _DeterministicExit:
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Сразу вывести traceback ошибки до принудительного завершения pytest. Итоговый terminal
        reporter не успевает сформировать секцию failures до ``os._exit``, поэтому без этого
        hook CI показывает только символы ``F``/``E``.
        """
        if not report.failed or report.when not in {"setup", "call", "teardown"}:
            return
        sys.stderr.write(f"\nFAILED {report.nodeid} during {report.when}\n")
        sys.stderr.write(getattr(report, "longreprtext", str(report.longrepr)) + "\n")
        sys.stderr.flush()

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        """Выполнить операцию pytest sessionfinish for _DeterministicExit. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        del session
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(int(exitstatus))


def main() -> None:
    # The session-finish hook normally terminates the process.  The fallback is
    # retained for collection/configuration failures that occur before a
    # Session is created.
    """Запустить the run pytest command-line workflow. Arguments, exit status and user-visible
    diagnostics are handled here.
    """
    exit_code = int(pytest.main(sys.argv[1:], plugins=[_DeterministicExit()]))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)


if __name__ == "__main__":
    main()
