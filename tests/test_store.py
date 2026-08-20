"""Transactional BoardStore behavior."""

import pathlib
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import USERS

import api_endpoint
import board_state


def make_store(path: pathlib.Path) -> api_endpoint.BoardStore:
    """Persist an empty board and return its store."""
    board = board_state.Board(project="Store", users=USERS, browser_user="terry",
                         cli_user="claude", default_owner="claude")
    board_state.save(board, path)
    return api_endpoint.BoardStore(path)


def create_alpha(board: board_state.Board) -> str:
    """Representative store command."""
    return board.create("alpha", "Alpha", "backlog", "claude")


def test_success_increments_revision_after_save(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    store = make_store(path)
    result, revision = store.execute(0, create_alpha)

    saved = board_state.load(path)
    assert "created" in result
    assert revision == 1
    assert saved.revision == 1
    assert saved.find("alpha").subject == "Alpha"


def test_stale_revision_changes_nothing(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    store = make_store(path)
    store.execute(0, create_alpha)

    with pytest.raises(api_endpoint.RevisionConflict, match="revision is 1"):
        store.execute(0, lambda board: board.comment("alpha", "stale", "claude"))
    saved = board_state.load(path)
    assert saved.revision == 1
    assert saved.find("alpha").comments == []


def test_domain_refusal_changes_nothing(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    store = make_store(path)

    with pytest.raises(board_state.BoardError, match="may not create"):
        store.execute(0, lambda board: board.create(
            "alpha", "Alpha", "completed", "claude"))
    saved = board_state.load(path)
    assert saved.revision == 0
    assert saved.items == []


def test_save_failure_does_not_publish_candidate(
        tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "board.json"
    store = make_store(path)

    def fail_save(_board: board_state.Board, _path: pathlib.Path) -> None:
        raise OSError("injected save failure")

    monkeypatch.setattr(board_state, "save", fail_save)
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
    assert isinstance(failures[0], api_endpoint.RevisionConflict)
    saved = board_state.load(path)
    assert saved.revision == 1
    assert len(saved.items) == 1


def test_source_checkout_requires_ignored_board_directory() -> None:
    root = pathlib.Path(api_endpoint.__file__).resolve().parent
    assert "refusing board data" in api_endpoint.source_checkout_board_problem(
        root / "sensitive-board.json")
    assert "refusing board data" in api_endpoint.source_checkout_board_problem(
        root / ".venv" / "private.json")
    assert api_endpoint.source_checkout_board_problem(root / "boards" / "private.json") == ""


def test_source_checkout_refuses_boards_directory_when_ignore_rule_is_ineffective(
        monkeypatch: pytest.MonkeyPatch) -> None:
    root = pathlib.Path(api_endpoint.__file__).resolve().parent

    def fake_git(args: list[str], _cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(args, 0, str(root), "")
        if args[:2] == ["check-ignore", "--quiet"]:
            return subprocess.CompletedProcess(args, 1, "", "")
        raise AssertionError(f"unexpected git command: {args}")

    monkeypatch.setattr(api_endpoint, "_git", fake_git)
    problem = api_endpoint.source_checkout_board_problem(root / "boards" / "private.json")
    assert "git does not ignore" in problem


def test_board_outside_source_checkout_remains_supported(tmp_path: pathlib.Path) -> None:
    assert api_endpoint.source_checkout_board_problem(tmp_path / "board.json") == ""


def test_ignored_board_disables_autopush(tmp_path: pathlib.Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("/boards/\n", encoding="utf-8")
    board = tmp_path / "boards" / "private.json"
    board.parent.mkdir()
    assert api_endpoint.push_unavailable(board) == (
        "the board is ignored by git")
