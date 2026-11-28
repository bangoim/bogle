"""Who may import what around the spending code (issue #114), read from the source.

The Pluggy client is shared with #111 and knows nothing of spending; the
spending package never reaches into the portfolio; and only the frontends call
the spending package.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "bogle"

SPENDING_MAY_IMPORT = (
    "bogle.spending",
    "bogle.data.pluggy",
    "bogle.db",
    "bogle.settings",
    "bogle.format",
    "bogle.error_messages",
    "bogle.domain.errors",
)


def module_name(path: Path) -> str:
    parts = path.relative_to(SRC.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def bogle_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
            found.add(node.module)
    return {name for name in found if name == "bogle" or name.startswith("bogle.")}


def under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(f"{prefix}.")


def test_the_spending_package_imports_only_what_it_may() -> None:
    offenders = {
        module_name(path): sorted(
            name for name in bogle_imports(path) if not any(under(name, allowed) for allowed in SPENDING_MAY_IMPORT)
        )
        for path in (SRC / "spending").glob("*.py")
    }
    assert {module: names for module, names in offenders.items() if names} == {}


def test_the_pluggy_client_knows_nothing_of_spending() -> None:
    assert not any(under(name, "bogle.spending") for name in bogle_imports(SRC / "data" / "pluggy.py"))


def test_only_the_frontends_import_the_spending_package() -> None:
    importers = {
        module_name(path)
        for path in SRC.rglob("*.py")
        if not under(module_name(path), "bogle.spending")
        and any(under(name, "bogle.spending") for name in bogle_imports(path))
    }
    assert importers, "a CLI importa o pacote de gastos"
    assert all(under(module, "bogle.cli") or under(module, "bogle.tui") for module in importers), importers
