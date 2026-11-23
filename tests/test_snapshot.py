import json

from plotroom.core.snapshot import estimate_tokens, render_snapshot


def test_empty_board() -> None:
    board = render_snapshot("demo", 0, [], {}, 2000)
    assert board == {
        "project": "demo",
        "cursor": 0,
        "lanes": {},
        "open_items": {},
        "items": [],
        "left_out": 0,
    }


def test_estimator_counts_utf8_bytes() -> None:
    assert estimate_tokens("🧪🧪a") == 3
    assert estimate_tokens(json.dumps({})) == 1
