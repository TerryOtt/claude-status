"""JSON validation and atomic snapshot tests."""

import json
import pathlib

import pytest
from conftest import USERS

import board_state


def saved_board(path: pathlib.Path) -> board_state.Board:
    """Write one representative board and return it."""
    board = board_state.Board(project="Round trip", revision=7, users=USERS,
                         browser_user="terry", cli_user="claude",
                         default_owner="claude")
    board.create("alpha", "Alpha", "backlog", "claude", detail="Detail")
    board.comment("alpha", "Comment", "claude")
    board_state.save(board, path)
    return board


def test_save_load_round_trip(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    original = saved_board(path)
    assert board_state.load(path).to_json() == original.to_json()


def test_invalid_board_json_reports_parser_location(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    path.write_text('{\n  "schema": 2,\n  "items": nope\n}\n', encoding="utf-8")

    with pytest.raises(
        board_state.BoardError,
        match=r"board\.json: invalid JSON at line 3, column 12: Expecting value",
    ):
        board_state.load(path)


def test_duplicate_board_json_key_is_refused(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    path.write_text(
        '{"schema": 2, "schema": 2, "items": []}\n', encoding="utf-8")

    with pytest.raises(
        board_state.BoardError,
        match="duplicate JSON object key 'schema'",
    ):
        board_state.load(path)


def test_legacy_board_without_revision_loads_at_zero(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    board = saved_board(path)
    raw = board.to_json()
    del raw["revision"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert board_state.load(path).revision == 0


@pytest.mark.parametrize("revision", [-1, "1", 1.5, None])
def test_invalid_revision_is_refused(tmp_path: pathlib.Path, revision: object) -> None:
    path = tmp_path / "board.json"
    board = saved_board(path)
    raw = board.to_json()
    raw["revision"] = revision
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(board_state.BoardError, match="revision"):
        board_state.load(path)


def test_duplicate_id_is_refused(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    board = saved_board(path)
    raw = board.to_json()
    raw["items"].append(dict(raw["items"][0]))
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(board_state.BoardError, match="duplicate id"):
        board_state.load(path)


def test_duplicate_ticket_is_refused(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    board = saved_board(path)
    raw = board.to_json()
    duplicate = dict(raw["items"][0])
    duplicate["id"] = "beta"
    raw["items"].append(duplicate)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(board_state.BoardError, match="duplicate ticket"):
        board_state.load(path)


def test_rewound_ticket_counter_is_refused(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    board = saved_board(path)
    raw = board.to_json()
    raw["nextTicket"] = 1
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(board_state.BoardError, match="counter went backwards"):
        board_state.load(path)


def test_unknown_parent_is_refused(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    board = saved_board(path)
    raw = board.to_json()
    raw["items"][0]["parent"] = "missing"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(board_state.BoardError, match="unknown parent"):
        board_state.load(path)


def test_failed_replace_leaves_previous_snapshot(
        tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "board.json"
    original = saved_board(path).to_json()
    changed = board_state.load(path)
    changed.project = "Changed"

    def fail_replace(_tmp: pathlib.Path, _target: pathlib.Path) -> None:
        raise OSError("injected replacement failure")

    monkeypatch.setattr(board_state, "_replace_with_retry", fail_replace)
    with pytest.raises(OSError, match="injected"):
        board_state.save(changed, path)
    assert board_state.load(path).to_json() == original
    assert not list(tmp_path.glob("*.tmp*"))


def test_edit_locks_load_mutate_save_as_one_operation(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    saved_board(path)
    with board_state.edit(path) as board:
        board.project = "Committed"
    assert board_state.load(path).project == "Committed"


def test_check_edges_reports_no_inconsistent_permissions() -> None:
    assert board_state.check_edges() == []


def test_sort_self_check_is_clean() -> None:
    assert board_state.self_test_sort() == []
