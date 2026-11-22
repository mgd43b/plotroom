"""AD-1 dependency check: run in CI as ``python tests/depcheck.py``.

* ``plotroom.core`` imports only the standard library, ``plotroom.ports`` and itself.
* ``plotroom.ports`` imports only the standard library, itself and ``plotroom.core``.
* An adapter imports core, ports, third-party code and itself, never another adapter or ``app``.
* Only ``plotroom.app`` wires adapters.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = "plotroom"
STDLIB = set(sys.stdlib_module_names)


def module_name(path: Path, src: Path) -> str:
    parts = list(path.relative_to(src).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imported_modules(path: Path, module: str) -> set[str]:
    """Absolute dotted names imported by the file (relative imports resolved)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                target = ".".join([*base, *(node.module.split(".") if node.module else [])])
            else:
                target = node.module or ""
            found.add(target)
            # `from plotroom.adapters import mcp` names a submodule, not just a package
            found.update(f"{target}.{alias.name}" for alias in node.names)
    return found


def layer(module: str) -> tuple[str, str]:
    """``(layer, adapter name)`` for a ``plotroom`` module; the name is empty outside adapters."""
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != ROOT:
        return "root", ""
    if parts[1] == "adapters":
        return "adapter", parts[2] if len(parts) > 2 else ""
    return parts[1], ""


def violations(src: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted((src / ROOT).rglob("*.py")):
        module = module_name(path, src)
        own_layer, own_adapter = layer(module)
        for target in sorted(imported_modules(path, module)):
            top = target.split(".")[0]
            target_layer, target_adapter = layer(target)
            if top == ROOT:
                if own_layer == "core" and target_layer not in ("core", "ports"):
                    problems.append(f"{module} imports {target}: core may use only core and ports")
                elif own_layer == "ports" and target_layer not in ("ports", "core"):
                    problems.append(f"{module} imports {target}: ports may use only ports and core")
                elif own_layer == "adapter":
                    if target_layer == "app":
                        problems.append(f"{module} imports {target}: only app wires adapters")
                    elif target_layer == "adapter" and target_adapter != own_adapter:
                        problems.append(
                            f"{module} imports {target}: adapters never import each other"
                        )
            elif own_layer in ("core", "ports") and top not in STDLIB and top != "":
                problems.append(
                    f"{module} imports {target}: {own_layer} may use only the standard library"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    src = Path(args[0]) if args else Path(__file__).resolve().parent.parent / "src"
    problems = violations(src)
    for problem in problems:
        print(f"depcheck: {problem}")
    if problems:
        return 1
    print("depcheck: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
