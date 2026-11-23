import pytest

from plotroom.app.sqlite_floor import floor_problem, meets_floor


@pytest.mark.parametrize(
    "version",
    ["3.51.3", "3.51.4", "3.53.1", "3.60.0", "4.0.0", "3.44.6", "3.44.9", "3.50.7", "3.50.9"],
)
def test_at_or_above_the_floor(version: str) -> None:
    assert meets_floor(version)
    assert floor_problem(version) is None


@pytest.mark.parametrize(
    "version", ["3.50.4", "3.51.2", "3.51.0", "3.44.5", "3.50.6", "3.45.0", "3.46.1", "3.8.11"]
)
def test_below_the_floor(version: str) -> None:
    assert not meets_floor(version)
    problem = floor_problem(version)
    assert problem is not None
    assert version in problem


def test_the_interpreter_under_test_meets_the_floor() -> None:
    """CI runs uv-managed Python, whose bundled SQLite meets the floor."""
    assert floor_problem() is None
