from __future__ import annotations

"""Run a command in an isolated process group with deterministic timeout cleanup.

The release QA suite invokes migration, doctor and worker commands that may load
telemetry/database libraries.  In some instrumented container runtimes a child
helper can keep a pipe or process group alive after the main command exits.
This wrapper starts every command in its own session and always tears down the
whole group on timeout, while preserving the command's stdout/stderr and exit
code.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence


def _terminate_group(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    """Реализовать внутренний этап terminate group step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + max(0.0, grace_seconds)
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)

    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run(command: Sequence[str], timeout_seconds: float, kill_after_seconds: float) -> int:
    """Выполнить операцию run. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    if not command:
        raise ValueError("command is required")

    process = subprocess.Popen(list(command), start_new_session=True)
    try:
        return int(process.wait(timeout=timeout_seconds))
    except subprocess.TimeoutExpired:
        print(
            f"Command exceeded {timeout_seconds:g}s and was terminated: " + " ".join(command),
            file=sys.stderr,
        )
        _terminate_group(process, kill_after_seconds)
        try:
            process.wait(timeout=max(1.0, kill_after_seconds + 1.0))
        except subprocess.TimeoutExpired:
            # The process group has already received SIGKILL.  Do not let an
            # unrelated runtime teardown make release QA wait indefinitely.
            pass
        return 124
    except KeyboardInterrupt:
        _terminate_group(process, kill_after_seconds)
        return 130


def main() -> None:
    """Запустить the run command command-line workflow. Arguments, exit status and user-visible
    diagnostics are handled here.
    """
    parser = argparse.ArgumentParser(
        description="Run one command in an isolated process group with a timeout"
    )
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--kill-after", type=float, default=5.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if args.kill_after < 0:
        parser.error("--kill-after cannot be negative")

    raise SystemExit(run(command, args.timeout, args.kill_after))


if __name__ == "__main__":
    main()
