from plotroom.core.snapshot import render_empty_snapshot, snapshot_result


def test_empty_board() -> None:
    board = render_empty_snapshot("demo")
    assert board == {
        "project": "demo",
        "cursor": 0,
        "lanes": {},
        "open_items": {},
        "items": [],
        "left_out": 0,
    }


def test_snapshot_is_scoped_to_the_authenticated_project() -> None:
    assert snapshot_result("demo", None) == {"ok": True, "data": render_empty_snapshot("demo")}


def test_unavailable_wins() -> None:
    result = snapshot_result("demo", "SQLite too old")
    assert result == {"ok": False, "error": {"code": "unavailable", "message": "SQLite too old"}}
