"""TransitionPolicy ownership, isolation, and live reload behavior."""

import json
import os
import pathlib
from typing import Any, cast

import pytest
from conftest import USERS

import api_endpoint
import board_state


def policy_file(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    """Copy the production policy so a test may edit it independently."""
    path = tmp_path / name
    path.write_text(board_state.RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def remove_claude_backlog_promotion(path: pathlib.Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["edges"] = [
        edge for edge in doc["edges"]
        if not (edge["actor"] == "claude"
                and edge["from"] == "backlog"
                and edge["to"] == "ready_for_claude")
    ]
    old_stamp = path.stat().st_mtime_ns
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    os.utime(path, ns=(old_stamp + 1_000_000_000, old_stamp + 1_000_000_000))


def make_store(
    tmp_path: pathlib.Path, name: str, policy: board_state.TransitionPolicy,
) -> api_endpoint.BoardStore:
    path = tmp_path / f"{name}.json"
    board = board_state.Board(project=name, users=USERS, browser_user="terry",
                         cli_user="claude", default_owner="claude")
    board_state.save(board, path)
    return api_endpoint.BoardStore(path, policy)


def create_alpha(board: board_state.Board) -> str:
    return board.create("alpha", "Alpha", "backlog", "claude")


def test_transition_policy_cannot_be_mutated(tmp_path: pathlib.Path) -> None:
    policy = board_state.TransitionPolicy.load(policy_file(tmp_path, "rules.json"))

    with pytest.raises(TypeError):
        cast(Any, policy.table["backlog"].outbound)["completed"] = frozenset(
            {"claude"})


def test_stores_enforce_their_own_transition_policies(
    tmp_path: pathlib.Path,
) -> None:
    allowed_path = policy_file(tmp_path, "allowed-rules.json")
    denied_path = policy_file(tmp_path, "denied-rules.json")
    remove_claude_backlog_promotion(denied_path)

    allowed = make_store(tmp_path, "allowed", board_state.TransitionPolicy.load(allowed_path))
    denied = make_store(tmp_path, "denied", board_state.TransitionPolicy.load(denied_path))
    allowed.execute(0, create_alpha)
    denied.execute(0, create_alpha)

    allowed.execute(1, lambda board: board.move(
        "alpha", "ready_for_claude", "claude"))
    with pytest.raises(board_state.BoardError, match="not to ready_for_claude"):
        denied.execute(1, lambda board: board.move(
            "alpha", "ready_for_claude", "claude"))


def test_policy_reload_is_isolated_to_its_store(tmp_path: pathlib.Path) -> None:
    first_path = policy_file(tmp_path, "first-rules.json")
    second_path = policy_file(tmp_path, "second-rules.json")
    first = make_store(tmp_path, "first", board_state.TransitionPolicy.load(first_path))
    second = make_store(tmp_path, "second", board_state.TransitionPolicy.load(second_path))
    remove_claude_backlog_promotion(first_path)

    message = first.reload_policy_if_changed()

    assert message == "rules.json reloaded: 7 lanes"
    assert not first.policy.may_move("claude", "backlog", "ready_for_claude")
    assert second.policy.may_move("claude", "backlog", "ready_for_claude")
    assert first.snapshot().policy == first.policy


def test_bad_policy_reload_keeps_last_valid_policy_and_is_reported_once(
    tmp_path: pathlib.Path,
) -> None:
    rules_path = policy_file(tmp_path, "rules.json")
    store = make_store(tmp_path, "board", board_state.TransitionPolicy.load(rules_path))
    accepted = store.policy.rules
    old_stamp = rules_path.stat().st_mtime_ns
    rules_path.write_text('{"schema": -1}\n', encoding="utf-8")
    os.utime(rules_path,
             ns=(old_stamp + 1_000_000_000, old_stamp + 1_000_000_000))

    message = store.reload_policy_if_changed()

    assert message is not None and "changed and was REFUSED" in message
    assert store.policy.rules == accepted
    assert store.reload_policy_if_changed() is None
