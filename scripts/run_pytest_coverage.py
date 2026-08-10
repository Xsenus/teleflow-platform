from __future__ import annotations

"""Collect deterministic statement coverage for the TeleFlow test suite.

Release QA executes the complete functional suite separately with normal
fixture teardown.  Some managed Python 3.13 environments can deadlock inside
instrumented TestClient/SQLite teardown.  The coverage pass therefore saves
data after every collected test has completed its setup/call phase and exits
before instrumented teardown hooks can stall.  Test failures in setup/call are
still propagated; teardown correctness is covered by the preceding functional
pass.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")

import pytest  # noqa: E402
from coverage import Coverage  # noqa: E402


class _CoverageExit:
    def __init__(self, coverage: Coverage) -> None:
        """Инициализировать _CoverageExit with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.coverage = coverage
        self.total = 0
        self.completed: set[str] = set()
        self.failed = False
        self.finalized = False

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Выполнить операцию pytest collection finish for _CoverageExit. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        self.total = len(session.items)
        if self.total == 0:
            self._finish(5)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Выполнить операцию pytest runtest logreport for _CoverageExit. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        if report.failed and report.when in {"setup", "call"}:
            self.failed = True
            # Print the failure immediately because the deterministic exit skips
            # pytest's session-end failure summary in managed runtimes.
            sys.stderr.write(f"\nFAILED {report.nodeid} during {report.when}\n")
            sys.stderr.write(getattr(report, "longreprtext", str(report.longrepr)) + "\n")
            sys.stderr.flush()
        if report.when == "call" or (report.when == "setup" and (report.failed or report.skipped)):
            self.completed.add(report.nodeid)
            if len(self.completed) >= self.total:
                self._finish(1 if self.failed else 0)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        """Выполнить операцию pytest sessionfinish for _CoverageExit. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        del session
        self._finish(int(exitstatus))

    def _finish(self, exit_code: int) -> None:
        """Реализовать внутренний этап finish step for _CoverageExit. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        if self.finalized:
            return
        self.finalized = True
        try:
            self.coverage.stop()
            self.coverage.save()
        finally:
            sys.stdout.write(
                f"\nTeleFlow coverage runner: {len(self.completed)}/{self.total} tests measured\n"
            )
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(exit_code)


def main() -> None:
    """Запустить the run pytest coverage command-line workflow. Arguments, exit status and user-
    visible diagnostics are handled here.
    """
    data_file = os.environ.get("COVERAGE_FILE")
    if not data_file:
        raise SystemExit("COVERAGE_FILE is required")
    Path(data_file).parent.mkdir(parents=True, exist_ok=True)
    coverage = Coverage(source=["app"], data_file=data_file, config_file=False)
    coverage.start()
    plugin = _CoverageExit(coverage)
    exit_code = int(pytest.main(sys.argv[1:], plugins=[plugin]))
    plugin._finish(exit_code)


if __name__ == "__main__":
    main()
