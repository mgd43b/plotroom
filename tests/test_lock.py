from pathlib import Path

import pytest

from plotroom.app.lock import DataDirLock, LockError


def test_second_lock_on_the_same_directory_is_refused(tmp_path: Path) -> None:
    first = DataDirLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises(LockError, match="another plotroom serve owns"):
            DataDirLock(tmp_path).acquire()
    finally:
        first.release()


def test_lock_is_free_after_release(tmp_path: Path) -> None:
    with DataDirLock(tmp_path):
        pass
    with DataDirLock(tmp_path):
        pass


def test_different_directories_do_not_conflict(tmp_path: Path) -> None:
    with DataDirLock(tmp_path / "a"), DataDirLock(tmp_path / "b"):
        pass
