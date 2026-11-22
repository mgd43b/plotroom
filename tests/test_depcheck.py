from pathlib import Path

import depcheck


def tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / "src" / "plotroom" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root / "src"


def test_the_real_tree_is_clean() -> None:
    assert depcheck.violations(Path(__file__).resolve().parent.parent / "src") == []


def test_core_may_use_stdlib_ports_and_itself(tmp_path: Path) -> None:
    src = tree(
        tmp_path,
        {
            "core/a.py": "import json\nfrom plotroom.ports import Store\nfrom plotroom.core import b\nfrom . import b as c\n",
            "core/b.py": "",
            "ports/__init__.py": "",
        },
    )
    assert depcheck.violations(src) == []


def test_core_importing_third_party_or_an_adapter_or_app_is_a_violation(tmp_path: Path) -> None:
    src = tree(
        tmp_path,
        {
            "core/a.py": "import sqlalchemy\n",
            "core/b.py": "from plotroom.adapters.store_sqlite import SqliteStore\n",
            "core/c.py": "import plotroom.app.config\n",
        },
    )
    found = depcheck.violations(src)
    assert any("core.a imports sqlalchemy" in v for v in found)
    assert any("core.b imports plotroom.adapters.store_sqlite" in v for v in found)
    assert any("core.c imports plotroom.app.config" in v for v in found)


def test_relative_imports_are_resolved(tmp_path: Path) -> None:
    src = tree(
        tmp_path, {"core/a.py": "from ..adapters import mcp\n", "adapters/mcp/__init__.py": ""}
    )
    assert any("core.a imports plotroom.adapters" in v for v in depcheck.violations(src))


def test_adapters_may_use_core_ports_third_party_and_themselves(tmp_path: Path) -> None:
    src = tree(
        tmp_path,
        {
            "adapters/mcp/server.py": "import starlette\nfrom plotroom.core import x\nfrom plotroom.ports import Store\nfrom plotroom.adapters.mcp import proxy\nfrom . import proxy as p\n",
            "adapters/mcp/proxy.py": "",
        },
    )
    assert depcheck.violations(src) == []


def test_adapters_never_import_each_other_or_app(tmp_path: Path) -> None:
    src = tree(
        tmp_path,
        {
            "adapters/mcp/server.py": "from plotroom.adapters.store_sqlite import SqliteStore\nfrom plotroom.app import config\n",
            "adapters/store_sqlite/store.py": "",
        },
    )
    found = depcheck.violations(src)
    assert any("adapters never import each other" in v for v in found)
    assert any("only app wires adapters" in v for v in found)


def test_app_may_import_everything(tmp_path: Path) -> None:
    src = tree(
        tmp_path,
        {
            "app/serve.py": "from plotroom.adapters.mcp import build_app\nfrom plotroom.core import x\n",
            "adapters/mcp/__init__.py": "",
        },
    )
    assert depcheck.violations(src) == []


def test_main_exit_codes(tmp_path: Path, capsys: object) -> None:
    clean = tree(tmp_path / "clean", {"core/a.py": "import json\n"})
    dirty = tree(tmp_path / "dirty", {"core/a.py": "import httpx\n"})
    assert depcheck.main([str(clean)]) == 0
    assert depcheck.main([str(dirty)]) == 1


def test_an_adapter_may_import_its_own_package_through_the_parent(tmp_path: Path) -> None:
    src = tree(
        tmp_path,
        {
            "adapters/mcp/server.py": "from plotroom.adapters import mcp\n",
            "adapters/mcp/__init__.py": "",
            "adapters/store_sqlite/__init__.py": "",
        },
    )
    assert depcheck.violations(src) == []


def test_an_adapter_importing_another_through_the_parent_is_still_a_violation(
    tmp_path: Path,
) -> None:
    src = tree(
        tmp_path,
        {
            "adapters/mcp/server.py": "from plotroom.adapters import store_sqlite\n",
            "adapters/store_sqlite/__init__.py": "",
        },
    )
    assert any("adapters never import each other" in v for v in depcheck.violations(src))
