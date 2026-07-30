from __future__ import annotations

import ast
import shutil
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_TARGET = Path(r"E:\python\main\refactored_project_phase6\bot\handlers\auctions.py")
FUNCTION_NAME = "_send_user_pending_lot_preview"


def _line_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def patch_source(source: str) -> tuple[str, list[str]]:
    lines = source.splitlines(keepends=True)
    changes: list[str] = []

    starts = [
        i for i, line in enumerate(lines)
        if line.startswith(f"async def {FUNCTION_NAME}(")
    ]
    if not starts:
        raise RuntimeError(f"Функция {FUNCTION_NAME} не найдена")

    # Патчим все определения. Если в огромном legacy-файле внезапно есть дубль,
    # последняя версия всё равно не должна снова победить с неправильной сигнатурой.
    offset = 0
    for original_start in starts:
        start = original_start + offset
        end = start + 1
        while end < len(lines) and not lines[end].startswith(") -> None:"):
            end += 1
        if end >= len(lines):
            raise RuntimeError(f"Не найден конец сигнатуры {FUNCTION_NAME} у строки {start + 1}")

        signature = "".join(lines[start : end + 1])

        if "custom_offer_terms" not in signature:
            accepted_index = None
            for i in range(start, end):
                if "accepted_currencies:" in lines[i]:
                    accepted_index = i
                    break
            if accepted_index is None:
                raise RuntimeError("В сигнатуре не найден accepted_currencies")

            indent = _line_indent(lines[accepted_index])
            newline = "\r\n" if lines[accepted_index].endswith("\r\n") else "\n"
            lines.insert(
                accepted_index + 1,
                f"{indent}custom_offer_terms: str | None = None,{newline}",
            )
            changes.append("добавлен параметр custom_offer_terms")
            end += 1
            offset += 1

        # Значение по умолчанию упрощает совместимость со старыми вызовами.
        for i in range(start, end):
            stripped = lines[i].strip()
            if stripped.startswith("image_file_id:") and "= None" not in stripped:
                ending = "\r\n" if lines[i].endswith("\r\n") else "\n"
                comma = "," if stripped.endswith(",") else ""
                indent = _line_indent(lines[i])
                lines[i] = f"{indent}image_file_id: str | None = None{comma}{ending}"
                changes.append("image_file_id получил безопасное значение None")
                break

        # Найдём тело функции до следующего top-level def/async def/class.
        body_start = end + 1
        body_end = len(lines)
        for i in range(body_start, len(lines)):
            if (
                lines[i].startswith("def ")
                or lines[i].startswith("async def ")
                or lines[i].startswith("class ")
            ):
                body_end = i
                break

        body = "".join(lines[body_start:body_end])
        if "currency_choices_label(" in body and "custom_terms=custom_offer_terms" not in body:
            fallback_index = None
            for i in range(body_start, body_end):
                if "fallback=currency" in lines[i]:
                    fallback_index = i
                    break
            if fallback_index is None:
                raise RuntimeError("Не найден fallback=currency в превью валют")
            indent = _line_indent(lines[fallback_index])
            newline = "\r\n" if lines[fallback_index].endswith("\r\n") else "\n"
            lines.insert(
                fallback_index + 1,
                f"{indent}custom_terms=custom_offer_terms,{newline}",
            )
            changes.append("custom_offer_terms добавлен в currency_choices_label")
            offset += 1

    patched = "".join(lines)
    compile(patched, "auctions.py", "exec")

    tree = ast.parse(patched)
    defs = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == FUNCTION_NAME
    ]
    if not defs:
        raise RuntimeError("Проверка AST не нашла функцию после исправления")

    for node in defs:
        kw_names = [arg.arg for arg in node.args.kwonlyargs]
        if "custom_offer_terms" not in kw_names:
            raise RuntimeError("AST-проверка: custom_offer_terms отсутствует в сигнатуре")

    return patched, changes


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET
    if not target.exists():
        print(f"ОШИБКА: файл не найден: {target}")
        return 2

    source = target.read_text(encoding="utf-8-sig")
    patched, changes = patch_source(source)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = target.with_name(f"{target.name}.backup_{timestamp}")
    shutil.copy2(target, backup)

    target.write_text(patched, encoding="utf-8", newline="")

    pycache = target.parent / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)

    # Финальная проверка уже записанного файла.
    written = target.read_text(encoding="utf-8")
    compile(written, str(target), "exec")
    tree = ast.parse(written)
    matched = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == FUNCTION_NAME
    ]

    print("HOTFIX УСПЕШНО ПРИМЕНЁН")
    print(f"Файл: {target}")
    print(f"Резервная копия: {backup}")
    print(f"Определений функции проверено: {len(matched)}")
    if changes:
        for item in changes:
            print(f"- {item}")
    else:
        print("- файл уже содержал исправление; выполнена повторная проверка")
    print("- синтаксис Python проверен")
    print("- __pycache__ удалён")
    print("Теперь полностью остановите старый процесс бота и запустите main.py заново.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
