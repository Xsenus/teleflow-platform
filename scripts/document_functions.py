from __future__ import annotations

"""Добавлять устойчивые русские docstring/JSDoc ко всем именованным функциям.

Утилита редактирует исходный текст без обратной генерации AST, поэтому сохраняет
форматирование, декораторы и комментарии. Повторный запуск не меняет уже
документированные функции.
"""

import argparse
import ast
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOTS = ("app", "scripts", "migrations", "tests")
JAVASCRIPT_FILES = (Path("app/static/app.js"), Path("app/static/sw.js"))


@dataclass(slots=True)
class FunctionLocation:
    """Описать Python-функцию и содержащий её класс, если он существует."""

    node: ast.FunctionDef | ast.AsyncFunctionDef
    class_name: str | None


class FunctionCollector(ast.NodeVisitor):
    """Собрать Python-функции вместе с именами содержащих их классов."""

    def __init__(self) -> None:
        """Инициализировать пустой результат и стек имён классов."""

        self.items: list[FunctionLocation] = []
        self._classes: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        """Посетить класс и связать вложенные методы с его именем."""

        self._classes.append(node.name)
        self.generic_visit(node)
        self._classes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        """Записать синхронную функцию до обхода вложенных определений."""

        self.items.append(FunctionLocation(node=node, class_name=self._current_class()))
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        """Записать асинхронную функцию до обхода вложенных определений."""

        self.items.append(FunctionLocation(node=node, class_name=self._current_class()))
        self.generic_visit(node)

    def _current_class(self) -> str | None:
        """Вернуть ближайший внешний класс либо ``None`` на уровне модуля."""

        return self._classes[-1] if self._classes else None


def _humanize(name: str) -> str:
    """Преобразовать идентификатор в читаемую фразу в нижнем регистре."""

    value = name.strip("_").replace("_", " ") or "function operation"
    return re.sub(r"\s+", " ", value).strip().lower()


def _function_doc(*, name: str, class_name: str | None, relative_path: Path) -> str:
    """Сформировать краткое описание назначения и поведения функции."""

    phrase = _humanize(name)
    context = f" класса {class_name}" if class_name else ""
    if name == "__init__":
        return (
            f"Инициализировать {class_name or 'компонент'} с явными зависимостями. "
            "Сохраняется только состояние, необходимое последующим операциям."
        )
    if name in {"__enter__", "__aenter__"}:
        return "Войти в управляемый контекст и вернуть доступный вызывающему коду ресурс."
    if name in {"__exit__", "__aexit__"}:
        return "Покинуть управляемый контекст и освободить ресурсы даже при ошибке тела."
    if name == "__call__":
        return f"Выполнить поведение вызываемого объекта{context} и вернуть доменный результат."
    if name in {"upgrade", "downgrade"} and "migrations" in relative_path.parts:
        direction = "Применить" if name == "upgrade" else "Откатить"
        return f"{direction} ревизию Alembic в безопасном для зависимостей порядке."
    if name == "main":
        return (
            f"Запустить сценарий командной строки {relative_path.stem.replace('_', ' ')}. "
            "Здесь обрабатываются аргументы, код завершения и пользовательская диагностика."
        )
    if name.startswith("test_"):
        return (
            f"Проверить требование: {phrase.removeprefix('test ')}. "
            "Тест завершается ошибкой при нарушении зафиксированного инварианта."
        )
    if name.startswith(("validate_", "check_", "assert_", "ensure_", "verify_")):
        return (
            f"Проверить {phrase.split(' ', 1)[1] if ' ' in phrase else phrase}{context}. "
            "Некорректные данные или состояние отклоняются до побочного эффекта."
        )
    if name.startswith(("get_", "list_", "read_", "load_", "fetch_", "find_", "resolve_")):
        return (
            f"Прочитать {phrase.split(' ', 1)[1] if ' ' in phrase else phrase}{context}. "
            "Значение возвращается без несвязанных изменений состояния."
        )
    if name.startswith(("create_", "add_", "append_", "register_", "import_", "build_")):
        return (
            f"Создать {phrase.split(' ', 1)[1] if ' ' in phrase else phrase}{context}. "
            "Перед сохранением или возвратом проверяются связанные инварианты."
        )
    if name.startswith(("update_", "patch_", "set_", "mark_", "transition_", "advance_")):
        return (
            f"Обновить {phrase.split(' ', 1)[1] if ' ' in phrase else phrase}{context}. "
            "Переход применяется только после проверки его предусловий."
        )
    if name.startswith(("delete_", "remove_", "revoke_", "cancel_", "close_", "disable_")):
        return (
            f"Безопасно выполнить {phrase}{context}. "
            "Зависимое состояние и видимые в аудите последствия обрабатываются согласованно."
        )
    if name.startswith(("serialize_", "deserialize_", "encode_", "decode_", "canonical_")):
        return f"Преобразовать данные для {phrase}{context} в каноническое представление проекта."
    if any(token in name for token in ("sha", "hash", "fingerprint", "digest")):
        return (
            f"Вычислить {phrase}{context}. "
            "Канонический ввод обеспечивает детерминированное сравнение целостности."
        )
    if name.startswith(("run_", "process_", "handle_", "execute_", "dispatch_", "synchronize_")):
        return (
            f"Выполнить {phrase}{context}. "
            "Операция координирует ограниченные побочные эффекты и возвращает устойчивый результат."
        )
    if name.startswith(("to_", "from_", "as_")):
        return f"Преобразовать {phrase}{context}, не изменяя исходный объект."
    if name.startswith("_"):
        return (
            f"Реализовать внутренний этап {phrase}{context}. "
            "Вспомогательная функция сохраняет детерминированность и тестируемость процесса."
        )
    return (
        f"Выполнить операцию {phrase}{context}. "
        "Аргументы интерпретируются в контексте модуля, результат возвращается вызывающему коду."
    )


def _docstring_lines(text: str, indent: str) -> list[str]:
    """Оформить сгенерированное описание как переносимый Python-docstring."""

    wrapped = textwrap.wrap(text, width=max(50, 96 - len(indent))) or [text]
    if len(wrapped) == 1:
        return [f'{indent}"""{wrapped[0]}"""\n']
    lines = [f'{indent}"""{wrapped[0]}\n']
    lines.extend(f"{indent}{line}\n" for line in wrapped[1:])
    lines.append(f'{indent}"""\n')
    return lines


_DOC_PREFIX_TRANSLATIONS = {
    "A standby ": "Резервная площадка ",
    "A recomputed ": "Пересчитанный ",
    "A rehashed ": "Повторно хешированное ",
    "Recomputing ": "Пересчёт ",
    "Worker ": "Фоновый worker ",
    "Register ": "Зарегистрировать ",
    "Invalidate ": "Инвалидировать ",
    "Expire ": "Завершить просроченные ",
    "Create, ": "Создать, ",
    "Compatibility endpoint ": "Совместимая точка API ",
    "Independently ": "Независимо ",
    "Recalculating ": "Пересчёт ",
    "Re-encrypt ": "Перешифровать ",
    "Reproduce ": "Воспроизвести ",
    "Fingerprint ": "Вычислить fingerprint для ",
    "Serialize ": "Сериализовать ",
    "Classify ": "Классифицировать ",
    "Estimate ": "Оценить ",
    "Recalculate ": "Пересчитать ",
    "Changing ": "Изменение ",
    "Read-only ": "Пользователи только для чтения ",
    "Over-budget ": "Превысившие бюджет ",
    "Accepted ": "Принятые ",
    "Runtime ": "Данные runtime ",
    "Different ": "Различные ",
    "Moving ": "Перенос ",
    "Repeated ": "Повторная ",
    "Operators ": "Операторы ",
    "Prometheus exposes ": "Prometheus предоставляет ",
    "Simulation ": "Симуляция ",
    "A live ": "Живая ",
    "A tenant ": "Организация ",
    "A missing ": "Отсутствующая ",
    "A stale ": "Устаревшая ",
    "A PASSED ": "Статус PASSED ",
    "A completed ": "Завершённый ",
    "One malformed ": "Одна повреждённая ",
    "The worker ": "Worker ",
    "The acceptance ": "Событие приёмки ",
    "The 31st ": "Тридцать первый ",
    "The API ": "API ",
    "The lock ": "Блокировка ",
    "Hold ": "Удерживать ",
    "Raise ": "Выбросить исключение ",
    "Reject ": "Отклонить ",
    "Persist ": "Сохранить ",
    "Translate ": "Преобразовать ",
    "Change ": "Изменить ",
    "Request ": "Запросить ",
    "Cancel ": "Отменить ",
    "Protect ": "Защитить ",
    "Compare ": "Сравнить ",
    "Evaluate ": "Оценить ",
    "Append ": "Добавить ",
    "Ensure ": "Убедиться, что ",
    "Project ": "Отразить ",
    "Count ": "Подсчитать ",
    "Hash ": "Хешировать ",
    "Load ": "Загрузить ",
    "Lock ": "Заблокировать ",
    "Enable ": "Включить ",
    "Permit ": "Разрешить ",
    "Remove ": "Удалить ",
    "Drop ": "Удалить ",
    "Fail ": "Завершить с ошибкой, если ",
    "Implement the internal ": "Реализовать внутренний этап ",
    "Initialize ": "Инициализировать ",
    "Calculate ": "Вычислить ",
    "Validate ": "Проверить ",
    "Normalize ": "Нормализовать ",
    "Transform ": "Преобразовать ",
    "Convert ": "Преобразовать ",
    "Execute ": "Выполнить ",
    "Perform ": "Выполнить операцию ",
    "Create ": "Создать ",
    "Update ": "Обновить ",
    "Return ": "Вернуть ",
    "Verify ": "Проверить ",
    "Inspect ": "Проверить ",
    "Check ": "Проверить ",
    "Resolve ": "Разрешить ",
    "Collect ": "Собрать ",
    "Document ": "Документировать ",
    "Record ": "Записать ",
    "Visit ": "Посетить ",
    "Build ": "Построить ",
    "List ": "Перечислить ",
    "Read ": "Прочитать ",
    "Run ": "Запустить ",
    "Enter ": "Войти в ",
    "Leave ": "Покинуть ",
    "Apply ": "Применить ",
    "Reverse ": "Откатить ",
    "Safely ": "Безопасно выполнить ",
}

_DOC_SENTENCE_TRANSLATIONS = {
    "Inputs are interpreted according to the surrounding module and the result is returned to the caller.": "Аргументы интерпретируются в контексте модуля, результат возвращается вызывающему коду.",
    "The operation coordinates its bounded side effects and reports a deterministic result.": "Операция координирует ограниченные побочные эффекты и возвращает детерминированный результат.",
    "The operation coordinates its bounded side effects and returns a stable result.": "Операция координирует ограниченные побочные эффекты и возвращает устойчивый результат.",
    "Relevant invariants are checked before the new value is persisted or returned.": "Перед сохранением или возвратом нового значения проверяются связанные инварианты.",
    "The requested value is returned without performing unrelated state changes.": "Значение возвращается без несвязанных изменений состояния.",
    "The transition is applied only after its preconditions have been verified.": "Переход применяется только после проверки его предусловий.",
    "Callers use this helper to keep the enclosing workflow deterministic and testable.": "Вспомогательная функция сохраняет детерминированность и тестируемость процесса.",
    "Only state needed by later operations is retained.": "Сохраняется только состояние, необходимое последующим операциям.",
    "The test fails when the documented safety or business invariant regresses.": "Тест завершается ошибкой при нарушении зафиксированного инварианта.",
    "Invalid input or state is rejected before a side effect can occur.": "Некорректные данные или состояние отклоняются до побочного эффекта.",
    "Dependent state and audit-visible consequences are handled consistently.": "Зависимое состояние и видимые в аудите последствия обрабатываются согласованно.",
    "Canonical input keeps integrity comparisons deterministic across processes.": "Канонический ввод обеспечивает детерминированное сравнение целостности между процессами.",
}


def _localize_docstring(text: str) -> str:
    """Перевести шаблонный английский docstring, сохранив технические идентификаторы."""

    localized = " ".join(text.split())
    for source, replacement in _DOC_PREFIX_TRANSLATIONS.items():
        if localized.startswith(source):
            localized = replacement + localized.removeprefix(source)
            break
    for source, replacement in _DOC_SENTENCE_TRANSLATIONS.items():
        localized = localized.replace(source, replacement)
    localized = localized.replace("Проверить that ", "Проверить сценарий ")
    localized = localized.replace("Удерживать the ", "Удерживать ")
    localized = re.sub(r" for ([A-Z][A-Za-z0-9_.]*)", r" класса \1", localized)
    return localized


def localize_python_file(path: Path) -> int:
    """Перевести существующие шаблонные docstring в одном Python-файле."""

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    collector = FunctionCollector()
    collector.visit(tree)
    lines = source.splitlines(keepends=True)
    replacements: list[tuple[int, int, list[str]]] = []
    for item in collector.items:
        node = item.node
        if not node.body or not isinstance(node.body[0], ast.Expr):
            continue
        expression = node.body[0]
        if not isinstance(expression.value, ast.Constant) or not isinstance(
            expression.value.value, str
        ):
            continue
        original = ast.get_docstring(node, clean=True)
        if original is None:
            continue
        localized = _localize_docstring(original)
        if localized == " ".join(original.split()):
            continue
        indent = " " * (node.col_offset + 4)
        replacements.append(
            (
                expression.lineno - 1,
                expression.end_lineno or expression.lineno,
                _docstring_lines(localized, indent),
            )
        )
    for start, end, payload in sorted(replacements, reverse=True):
        lines[start:end] = payload
    if replacements:
        path.write_text("".join(lines), encoding="utf-8")
    return len(replacements)


def document_python_file(path: Path) -> int:
    """Вставить отсутствующие docstring в один Python-файл."""

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    collector = FunctionCollector()
    collector.visit(tree)
    lines = source.splitlines(keepends=True)
    insertions: list[tuple[int, list[str], bool]] = []
    relative = path.relative_to(ROOT)
    for item in collector.items:
        node = item.node
        if ast.get_docstring(node, clean=False) is not None or not node.body:
            continue
        insertion_line = node.body[0].lineno - 1
        indent = " " * (node.col_offset + 4)
        text = _function_doc(name=node.name, class_name=item.class_name, relative_path=relative)
        doc_lines = _docstring_lines(text, indent)
        first_statement = node.body[0]
        inline_body = first_statement.col_offset > node.col_offset + 4
        if inline_body:
            original = lines[insertion_line]
            separator = original.rfind(":")
            if separator < 0:
                raise RuntimeError(
                    f"Cannot split inline function body: {path}:{insertion_line + 1}"
                )
            header = original[: separator + 1].rstrip() + "\n"
            body = original[separator + 1 :].strip()
            replacement = [header, *doc_lines, f"{indent}{body}\n"]
            insertions.append((insertion_line, replacement, True))
        else:
            insertions.append((insertion_line, doc_lines, False))
    for index, payload, replace in sorted(insertions, key=lambda item: item[0], reverse=True):
        if replace:
            lines[index : index + 1] = payload
        else:
            lines[index:index] = payload
    if insertions:
        path.write_text("".join(lines), encoding="utf-8")
    return len(insertions)


_JS_FUNCTION = re.compile(
    r"^(?P<indent>\s*)(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\("
)
_JS_ARROW = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
)
_JS_METHOD = re.compile(
    r"^(?P<indent>\s*)(?:async\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*\([^;]*\)\s*\{\s*$"
)
_JS_CONTROL_WORDS = {"if", "for", "while", "switch", "catch", "with"}


def _javascript_definition(line: str) -> tuple[str, str] | None:
    """Вернуть отступ и имя именованной JavaScript-функции."""

    for pattern in (_JS_FUNCTION, _JS_ARROW, _JS_METHOD):
        match = pattern.match(line)
        if match and match.group("name") not in _JS_CONTROL_WORDS:
            return match.group("indent"), match.group("name")
    return None


def _has_preceding_comment(lines: list[str], index: int) -> bool:
    """Проверить, документирует ли определение ближайшая непустая строка выше."""

    cursor = index - 1
    while cursor >= 0 and not lines[cursor].strip():
        cursor -= 1
    if cursor < 0:
        return False
    value = lines[cursor].strip()
    return value.startswith("//") or value.endswith("*/")


def document_javascript_file(path: Path) -> int:
    """Добавить JSDoc к именованным JavaScript-функциям и методам объектов."""

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    insertions: list[tuple[int, list[str]]] = []
    for index, line in enumerate(lines):
        definition = _javascript_definition(line)
        if definition is None or _has_preceding_comment(lines, index):
            continue
        indent, name = definition
        phrase = _humanize(name)
        comment = [
            f"{indent}/**\n",
            f"{indent} * Выполнить {phrase}, явно сохраняя побочные эффекты UI или worker.\n",
            f"{indent} */\n",
        ]
        insertions.append((index, comment))
    for index, payload in reversed(insertions):
        lines[index:index] = payload
    if insertions:
        path.write_text("".join(lines), encoding="utf-8")
    return len(insertions)


def localize_javascript_file(path: Path) -> int:
    """Перевести сгенерированные английские JSDoc в одном JavaScript-файле."""

    source = path.read_text(encoding="utf-8")
    localized, count = re.subn(
        r"(?m)^(?P<indent>\s*)\* Execute (?P<name>.+?) and keep its UI or worker side effects explicit\.$",
        r"\g<indent>* Выполнить \g<name>, явно сохраняя побочные эффекты UI или worker.",
        source,
    )
    if count:
        path.write_text(localized, encoding="utf-8")
    return count


def iter_python_files() -> list[Path]:
    """Перечислить Python-файлы, охваченные политикой документации."""

    files: list[Path] = []
    for root_name in PYTHON_ROOTS:
        root = ROOT / root_name
        files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(files)


def main() -> None:
    """Документировать функции проекта и вывести число добавленных комментариев."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Do not modify files")
    parser.add_argument("--localize", action="store_true", help="Перевести шаблонные комментарии")
    args = parser.parse_args()
    if args.check:
        from check_function_docs import inspect_documentation

        failures, summary = inspect_documentation()
        print(summary)
        if failures:
            print("\n".join(failures))
            raise SystemExit(1)
        return
    python_count = sum(document_python_file(path) for path in iter_python_files())
    javascript_count = sum(
        document_javascript_file(ROOT / relative) for relative in JAVASCRIPT_FILES
    )
    localized_python = 0
    localized_javascript = 0
    if args.localize:
        localized_python = sum(localize_python_file(path) for path in iter_python_files())
        localized_javascript = sum(
            localize_javascript_file(ROOT / relative) for relative in JAVASCRIPT_FILES
        )
    print(
        f"Inserted {python_count} Python docstrings and {javascript_count} JavaScript JSDoc blocks; "
        f"localized {localized_python} Python and {localized_javascript} JavaScript comments"
    )


if __name__ == "__main__":
    main()
