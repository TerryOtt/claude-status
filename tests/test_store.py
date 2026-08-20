"""Transactional BoardStore behavior."""

import pathlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import USERS

import serve
import status


def make_store(path: pathlib.Path) -> serve.BoardStore:
    """Persist an empty board and return its store."""
    board = status.Board(project="Store", users=USERS, browser_user="terry",
                         cli_user="claude", default_owner="claude")
    status.save(board, path)
    return serve.BoardStore(path)


def create_alpha(board: status.Board) -> str:
    """Representative store command."""
    return board.create("alpha", "Alpha", "backlog", "claude")


def test_success_increments_revision_after_save(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    store = make_store(path)
    result, revision = store.execute(0, create_alpha)

    saved = status.load(path)
    assert "created" in result
    assert revision == 1
    assert saved.revision == 1
    assert saved.find("alpha").subject == "Alpha"


def test_stale_revision_changes_nothing(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    store = make_store(path)
    store.execute(0, create_alpha)

    with pytest.raises(serve.RevisionConflict, match="revision is 1"):
        store.execute(0, lambda board: board.comment("alpha", "stale", "claude"))
    saved = status.load(path)
    assert saved.revision == 1
    assert saved.find("alpha").comments == []


def test_domain_refusal_changes_nothing(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    store = make_store(path)

    with pytest.raises(status.BoardError, match="may not create"):
        store.execute(0, lambda board: board.create(
            "alpha", "Alpha", "completed", "claude"))
    saved = status.load(path)
    assert saved.revision == 0
    assert saved.items == []


def test_save_failure_does_not_publish_candidate(
        tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "board.json"
    store = make_store(path)

    def fail_save(_board: status.Board, _path: pathlib.Path) -> None:
        raise OSError("injected save failure")

    monkeypatch.setattr(status, "save", fail_save)
    with pytest.raises(OSError, match="injected"):
        store.execute(0, create_alpha)

    monkeypatch.undo()
    assert store.snapshot().revision == 0
    assert store.snapshot().items == []


def test_same_revision_concurrency_has_one_winner(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    store = make_store(path)

    def command(name: str) -> tuple[str, int]:
        return store.execute(0, lambda board: board.create(
            name, name.title(), "backlog", "claude"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(command, name) for name in ("alpha", "beta")]

    successes = [future.result() for future in futures if future.exception() is None]
    failures = [future.exception() for future in futures if future.exception() is not None]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], serve.RevisionConflict)
    saved = status.load(path)
    assert saved.revision == 1
    assert len(saved.items) == 1
