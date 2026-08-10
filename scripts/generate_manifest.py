from __future__ import annotations

"""Создавать и проверять детерминированный SHA-256 manifest релизного дерева."""

import argparse
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.sha256"


def candidate_files() -> list[Path]:
    """Получить отсортированные tracked/untracked файлы с учётом `.gitignore`."""

    completed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    paths = {
        Path(item.decode("utf-8"))
        for item in completed.stdout.split(b"\0")
        if item and Path(item.decode("utf-8")) != MANIFEST.relative_to(ROOT)
    }
    return sorted(paths, key=lambda path: path.as_posix())


def render_manifest() -> str:
    """Вычислить manifest без включения самого файла manifest."""

    lines = []
    for relative in candidate_files():
        digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative.as_posix()}")
    return "\n".join(lines) + "\n"


def main() -> None:
    """Обновить manifest либо проверить его без изменения рабочего дерева."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Только проверить manifest")
    args = parser.parse_args()
    expected = render_manifest()
    if args.check:
        actual = MANIFEST.read_text(encoding="utf-8") if MANIFEST.exists() else ""
        if actual != expected:
            raise SystemExit("MANIFEST.sha256 устарел; запустите scripts/generate_manifest.py")
        print(f"MANIFEST.sha256 проверен: {len(expected.splitlines())} файлов")
        return
    MANIFEST.write_text(expected, encoding="utf-8", newline="\n")
    print(f"MANIFEST.sha256 обновлён: {len(expected.splitlines())} файлов")


if __name__ == "__main__":
    main()
