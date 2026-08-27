import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1] / "src" / "gvas"
FORBIDDEN = {
    "gvas.infrastructure",
    "gvas.interfaces",
    "sqlalchemy",
    "fastapi",
    "slack_sdk",
    "twilio",
    "openai",
    "boto3",
}
ALLOWED_EXTERNAL = {"pydantic"}


def test_domain_application_boundaries() -> None:
    for layer in ("domain", "application"):
        for path in (ROOT / layer).rglob("*.py"):
            source = path.read_text()
            lowered = source.lower()
            assert "slack" not in lowered, f"{path}: forbidden transport substring slack"
            assert "twilio" not in lowered, f"{path}: forbidden transport substring twilio"
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                imported: str | None = None
                if isinstance(node, ast.Import):
                    imported = node.names[0].name
                elif isinstance(node, ast.ImportFrom):
                    imported = node.module
                if imported is not None:
                    assert not any(
                        imported == denied or imported.startswith(f"{denied}.")
                        for denied in FORBIDDEN
                    ), f"{path}: forbidden import {imported}"
                    top_level = imported.split(".", 1)[0]
                    is_stdlib = top_level in sys.stdlib_module_names
                    is_allowed_domain = imported == "gvas.domain" or imported.startswith(
                        "gvas.domain."
                    )
                    assert is_stdlib or top_level in ALLOWED_EXTERNAL or is_allowed_domain, (
                        f"{path}: disallowed import {imported}"
                    )
                    if (
                        layer == "application"
                        and imported.startswith("gvas.")
                        and not is_allowed_domain
                    ):
                        raise AssertionError(f"{path}: non-domain import {imported}")
