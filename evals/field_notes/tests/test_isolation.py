"""The evaluation suite and the product must not depend on each other."""

from __future__ import annotations

import ast
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = EVAL_ROOT.parents[1] / "src"


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_evaluation_code_never_imports_the_product() -> None:
    for path in sorted(EVAL_ROOT.rglob("*.py")):
        assert "gvas" not in _imported_roots(path), f"{path} imports the product"


def test_product_code_never_imports_the_evaluation_suite() -> None:
    for path in sorted(SRC_ROOT.rglob("*.py")):
        assert "field_notes" not in _imported_roots(path), f"{path} imports the eval suite"


def test_evaluation_code_ships_no_http_client_or_provider_sdk() -> None:
    forbidden = {"httpx", "requests", "urllib", "socket", "openai", "anthropic", "boto3"}
    for path in sorted(EVAL_ROOT.rglob("*.py")):
        offending = forbidden & _imported_roots(path)
        assert not offending, f"{path} imports {sorted(offending)}"
