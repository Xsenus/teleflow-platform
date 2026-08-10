from __future__ import annotations

"""Fail release QA when a named project function lacks useful documentation."""

import ast
import re

from document_functions import (
    JAVASCRIPT_FILES,
    ROOT,
    _has_preceding_comment,
    _javascript_definition,
    iter_python_files,
)

FORBIDDEN_PLACEHOLDERS = ("todo", "fixme", "describe this function")
RUSSIAN_TEXT = re.compile(r"[А-Яа-яЁё]")


def _preceding_comment(lines: list[str], index: int) -> str:
    """Вернуть ближайший однострочный комментарий или полный блок JSDoc."""

    cursor = index - 1
    while cursor >= 0 and not lines[cursor].strip():
        cursor -= 1
    if cursor < 0:
        return ""
    if lines[cursor].strip().startswith("//"):
        return lines[cursor]
    if not lines[cursor].strip().endswith("*/"):
        return ""
    end = cursor
    while cursor >= 0 and "/**" not in lines[cursor]:
        cursor -= 1
    return "\n".join(lines[cursor : end + 1]) if cursor >= 0 else ""


def inspect_documentation() -> tuple[list[str], str]:
    """Проверить Python docstrings and JavaScript JSDoc without modifying sources."""

    failures: list[str] = []
    python_total = 0
    javascript_total = 0
    for path in iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            python_total += 1
            doc = ast.get_docstring(node, clean=True)
            relative = path.relative_to(ROOT)
            if not doc:
                failures.append(f"{relative}:{node.lineno}: {node.name} has no docstring")
                continue
            normalized = doc.strip().lower()
            if len(normalized) < 20 or any(item in normalized for item in FORBIDDEN_PLACEHOLDERS):
                failures.append(f"{relative}:{node.lineno}: {node.name} has an unhelpful docstring")
            elif not RUSSIAN_TEXT.search(doc):
                failures.append(f"{relative}:{node.lineno}: {node.name} has no Russian explanation")
    for relative in JAVASCRIPT_FILES:
        path = ROOT / relative
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            definition = _javascript_definition(line)
            if definition is None:
                continue
            javascript_total += 1
            _indent, name = definition
            if not _has_preceding_comment(lines, index):
                failures.append(f"{relative}:{index + 1}: {name} has no JSDoc/comment")
            elif not RUSSIAN_TEXT.search(_preceding_comment(lines, index)):
                failures.append(f"{relative}:{index + 1}: {name} has no Russian JSDoc/comment")
    summary = (
        f"Documented functions: Python={python_total}, "
        f"JavaScript named={javascript_total}, failures={len(failures)}"
    )
    return failures, summary


def main() -> None:
    """Запустить the documentation gate and return a non-zero status on any omission."""

    failures, summary = inspect_documentation()
    print(summary)
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
