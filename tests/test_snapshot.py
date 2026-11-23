import json

import pytest

from plotroom.core.envelope import ok
from plotroom.core.events import LedgerError
from plotroom.core.lanes import Lane
from plotroom.core.next_items import Item
from plotroom.core.snapshot import estimate_tokens, render_next, render_search, render_snapshot
from plotroom.ports.search import SearchHit, SearchPage


def test_empty_board() -> None:
    board = render_snapshot("demo", 0, [], {}, 2000)
    assert board == {
        "project": "demo",
        "cursor": 0,
        "lanes": {},
        "open_items": {},
        "claims": 0,
        "overlaps": 0,
        "items": [],
        "left_out": 0,
    }


def test_estimator_counts_utf8_bytes() -> None:
    assert estimate_tokens("🧪🧪a") == 3
    assert estimate_tokens(json.dumps({})) == 1


def test_search_keeps_its_top_hit_when_its_excerpt_exceeds_the_budget() -> None:
    first = SearchHit("evt_first", "switchboard.message", "needle " + "🧪" * 233)
    second = SearchHit("evt_second", "switchboard.message", "another needle")
    page = render_search(SearchPage((first, second), 3), 256)
    assert page["hits"][0] == {
        "id": first.id,
        "kind": first.kind,
        "excerpt": first.excerpt[:80] + "…",
    }
    assert page["hits"][1]["id"] == second.id
    assert page["omitted"] == 1
    assert estimate_tokens(json.dumps(ok(page), ensure_ascii=False, indent=2)) <= 256


def test_next_pages_include_ids_for_oversize_items() -> None:
    items = [
        Item(
            f"item_{i}",
            "fixture",
            str(i),
            str(i),
            "orchestrator",
            1,
            "open",
            i,
            None,
            i,
            {"text": "x" * 1024},
            (),
        )
        for i in range(1, 20)
    ]
    offset = 0
    ids: list[str] = []
    while offset < len(items):
        page = render_next(len(items), items[offset:], offset, 256)
        assert estimate_tokens(json.dumps(ok(page), indent=2)) <= 256
        assert all(row["details_omitted"] for row in page["items"])
        ids.extend(row["id"] for row in page["items"])
        following: int = page.get("next_offset", len(items))
        assert following > offset
        offset = following
    assert ids == [item.id for item in items]


def test_empty_lane_page_metadata_is_included_in_the_budget() -> None:
    lane = Lane("lane", "fixture", (), "demo")
    counts = {"active": 1}
    summary = render_snapshot("demo", 1, [], counts, 2000)
    summary_budget = estimate_tokens(json.dumps(ok(summary), indent=2))
    with pytest.raises(LedgerError, match="snapshot summary exceeds"):
        render_snapshot("demo", 1, [], counts, summary_budget, lane_details=[lane], lane_offset=1)
    page = render_snapshot("demo", 1, [], counts, 256, lane_details=[lane], lane_offset=1)
    assert page["lane_details"] == []
    assert estimate_tokens(json.dumps(ok(page), indent=2)) <= 256
