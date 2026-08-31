#!/usr/bin/env python3
"""REG-01 CI check — fail the build if banned diagnostic language appears
in the surfaces this can safely, mechanically police.

Swept:
  - README.md, in full.
  - Every route handler's docstring and its `summary=`/`description=`
    decorator kwargs, under app/api/v1/routes/ and app/routes/.
  - Every `Field(..., description=...)` call under app/schemas/.

Deliberately NOT swept — see app/core/language_guard.py's module
docstring for why: LLM prompts, internal module/function/variable names,
NER label taxonomies, and JSON response field *names*. A term inside a
disclaimer ("NOT a medical diagnosis") does not count as a violation —
see language_guard.scan_text.

Run directly: `python scripts/check_prohibited_terms.py`. Exits 1 and
prints `file:line: term` for each real finding.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))
from app.core.language_guard import scan_text  # noqa: E402

ROUTE_DIRS = [ROOT / "app" / "api" / "v1" / "routes", ROOT / "app" / "routes"]
SCHEMA_DIR = ROOT / "app" / "schemas"
README = ROOT / "README.md"
MAIN = ROOT / "app" / "main.py"


def _offset_to_line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _check_readme() -> list[tuple[str, int, str]]:
    if not README.exists():
        return []
    text = README.read_text(encoding="utf-8")
    return [
        (str(README.relative_to(ROOT)), _offset_to_line(text, offset), term)
        for offset, term in scan_text(text)
    ]


def _string_literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _check_route_file(path: Path) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return findings
    rel = str(path.relative_to(ROOT))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        docstring = ast.get_docstring(node)
        if docstring:
            # node.body[0] is the docstring's own Expr statement when
            # get_docstring found one — use its line, not the def's, so
            # findings point at the actual text.
            doc_lineno = node.body[0].lineno if node.body else node.lineno
            for offset, term in scan_text(docstring):
                findings.append((rel, doc_lineno + docstring[:offset].count("\n"), term))

        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            for kw in decorator.keywords:
                if kw.arg not in {"summary", "description"}:
                    continue
                literal = _string_literal(kw.value)
                if literal is None:
                    continue
                for offset, term in scan_text(literal):
                    findings.append((rel, decorator.lineno, term))

    return findings


def _check_schema_file(path: Path) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return findings
    rel = str(path.relative_to(ROOT))

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Field"):
            continue
        for kw in node.keywords:
            if kw.arg != "description":
                continue
            literal = _string_literal(kw.value)
            if literal is None:
                continue
            for offset, term in scan_text(literal):
                findings.append((rel, node.lineno, term))

    return findings


def _check_fastapi_app_metadata(path: Path) -> list[tuple[str, int, str]]:
    """Scan the FastAPI(title=..., description=...) constructor call —
    that's the top-level Swagger/OpenAPI page description, genuinely
    user-facing, but it isn't a route handler so _check_route_file's
    decorator-kwarg walk doesn't reach it."""
    findings: list[tuple[str, int, str]] = []
    if not path.exists():
        return findings
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return findings
    rel = str(path.relative_to(ROOT))

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FastAPI"):
            continue
        for kw in node.keywords:
            if kw.arg not in {"title", "description", "summary"}:
                continue
            literal = _string_literal(kw.value)
            if literal is None:
                continue
            for offset, term in scan_text(literal):
                findings.append((rel, node.lineno, term))

    return findings


def main() -> int:
    findings: list[tuple[str, int, str]] = []
    findings += _check_readme()
    findings += _check_fastapi_app_metadata(MAIN)

    for route_dir in ROUTE_DIRS:
        if route_dir.is_dir():
            for path in sorted(route_dir.glob("*.py")):
                findings += _check_route_file(path)

    if SCHEMA_DIR.is_dir():
        for path in sorted(SCHEMA_DIR.glob("*.py")):
            findings += _check_schema_file(path)

    if findings:
        for rel, line, term in findings:
            print(f"{rel}:{line}: prohibited term {term!r}")
        print(f"\n{len(findings)} finding(s). See app/core/language_guard.py.")
        return 1

    print("check_prohibited_terms: clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
