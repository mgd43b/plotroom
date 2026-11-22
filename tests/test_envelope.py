import pytest

from plotroom.core.envelope import ERROR_CODES, err, ok


def test_ok_wraps_data() -> None:
    assert ok({"a": 1}) == {"ok": True, "data": {"a": 1}}


def test_err_carries_code_and_message() -> None:
    assert err("not_found", "no such lane") == {
        "ok": False,
        "error": {"code": "not_found", "message": "no such lane"},
    }


def test_declared_codes() -> None:
    assert set(ERROR_CODES) == {"forbidden", "not_found", "conflict", "invalid", "unavailable"}


def test_undeclared_code_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown error code"):
        err("boom", "x")  # pyright: ignore[reportArgumentType]
