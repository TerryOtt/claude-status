"""Board behavior at the domain-model boundary."""

import pytest

import board_state


def test_create_assigns_ticket_history_and_owner(board: board_state.Board) -> None:
    board.create("alpha", "Alpha", "backlog", "claude")

    item = board.find("alpha")
    assert item.ticket == 1
    assert item.owner == "claude"
    assert item.history[0].kind == "lane"
    assert item.history[0].frm is None
    assert board.next_ticket == 2


@pytest.mark.parametrize("reference", ["alpha", "1", "#1", "0001"])
def test_find_accepts_slug_and_ticket_spellings(board: board_state.Board, reference: str) -> None:
    board.create("alpha", "Alpha", "backlog", "claude")
    assert board.find(reference).id == "alpha"


def test_create_refuses_duplicate_id(board: board_state.Board) -> None:
    board.create("alpha", "Alpha", "backlog", "claude")
    with pytest.raises(board_state.BoardError, match="duplicate id"):
        board.create("alpha", "Another", "backlog", "claude")


def test_create_refuses_forbidden_lane(board: board_state.Board) -> None:
    with pytest.raises(board_state.BoardError, match="may not create"):
        board.create("alpha", "Alpha", "completed", "claude")


def test_move_records_legal_transition(board: board_state.Board) -> None:
    board.create("alpha", "Alpha", "ready_for_claude", "claude")
    board.move("alpha", "in_progress", "claude")

    item = board.find("alpha")
    assert item.state == "in_progress"
    assert item.history[-1].frm == "ready_for_claude"
    assert item.history[-1].to == "in_progress"
    assert board.verify() == []


@pytest.mark.parametrize(("actor", "source", "destination"), [
    ("claude", "ready_for_claude", "in_progress"),
    ("claude", "in_progress", "ready_for_review"),
    ("claude", "blocked", "in_progress"),
    ("terry", "ready_for_review", "completed"),
])
def test_representative_allowed_edges(
        board: board_state.Board, actor: str, source: str, destination: str) -> None:
    item = board_state.Item("alpha", "Alpha", source, ticket=1, owner="claude")
    board.items.append(item)
    board.move("alpha", destination, actor)
    assert item.state == destination
    assert board.verify() == []


@pytest.mark.parametrize(("actor", "source", "destination"), [
    ("claude", "ready_for_review", "completed"),
    ("terry", "completed", "in_progress"),
    ("claude", "backlog", "completed"),
])
def test_representative_forbidden_edges(
        board: board_state.Board, actor: str, source: str, destination: str) -> None:
    item = board_state.Item("alpha", "Alpha", source, ticket=1, owner="claude")
    board.items.append(item)
    with pytest.raises(board_state.BoardError):
        board.move("alpha", destination, actor)
    assert item.state == source and item.history == []


def test_move_refuses_claude_signoff(board: board_state.Board) -> None:
    item = board_state.Item("alpha", "Alpha", "ready_for_review", ticket=1, owner="claude")
    board.items.append(item)
    with pytest.raises(board_state.BoardError, match="not to completed"):
        board.move("alpha", "completed", "claude")
    assert item.state == "ready_for_review"
    assert item.history == []


def test_assignment_and_priority_are_non_lane_history(board: board_state.Board) -> None:
    board.create("alpha", "Alpha", "backlog", "claude")
    board.assign("alpha", "terry", "claude")
    board.set_priority("alpha", "P1", "claude")

    item = board.find("alpha")
    assert [entry.kind for entry in item.history] == ["lane", "owner", "priority"]
    assert item.replayed_state() == "backlog"
    assert board.verify() == []


def test_comment_refuses_blank_text(board: board_state.Board) -> None:
    board.create("alpha", "Alpha", "backlog", "claude")
    with pytest.raises(board_state.BoardError, match="needs text"):
        board.comment("alpha", "  ", "terry")


def test_human_comment_creates_reference_but_bot_comment_does_not(
        board: board_state.Board) -> None:
    board.create("alpha", "Alpha", "backlog", "claude")
    board.create("beta", "Beta", "backlog", "claude")

    board.comment("alpha", "See #0002", "terry")
    assert board.links_for("alpha") == [("references", "beta")]
    assert board.links_for("beta") == [("referenced_by", "alpha")]

    board.links.clear()
    board.comment("alpha", "Explaining #0002", "claude")
    assert board.links == []


def test_link_is_stored_once_and_inverse_is_derived(board: board_state.Board) -> None:
    board.create("alpha", "Alpha", "backlog", "claude")
    board.create("beta", "Beta", "backlog", "claude")
    board.link("beta", "blocked_by", "alpha", "claude")

    assert len(board.links) == 1
    assert board.links_for("alpha") == [("blocks", "beta")]
    assert board.links_for("beta") == [("blocked_by", "alpha")]


def test_parent_cycle_is_refused_and_rolled_back(board: board_state.Board) -> None:
    board.create("alpha", "Alpha", "backlog", "claude")
    board.create("beta", "Beta", "backlog", "claude")
    board.set_parent("beta", "alpha", "claude")

    with pytest.raises(board_state.BoardError, match="parent cycle"):
        board.set_parent("alpha", "beta", "claude")
    assert board.find("alpha").parent is None
    assert board.find("beta").parent == "alpha"


def test_lanes_sort_by_priority_then_creation_then_ticket(board: board_state.Board) -> None:
    newer = board_state.Item(
        "newer", "Newer", "backlog", ticket=2, priority="P2", owner="claude",
        history=[board_state.Change("2026-01-02T00:00:00+00:00", "backlog", "claude")])
    older = board_state.Item(
        "older", "Older", "backlog", ticket=1, priority="P2", owner="claude",
        history=[board_state.Change("2026-01-01T00:00:00+00:00", "backlog", "claude")])
    urgent = board_state.Item(
        "urgent", "Urgent", "backlog", ticket=3, priority="P1", owner="claude",
        history=[board_state.Change("2026-01-03T00:00:00+00:00", "backlog", "claude")])
    board.items = [newer, older, urgent]

    backlog = next(lane for lane in board.lanes() if lane.state == "backlog")
    assert [item.id for item in backlog.items] == ["urgent", "older", "newer"]


def test_verify_detects_direct_state_write(board: board_state.Board) -> None:
    board.create("alpha", "Alpha", "backlog", "claude")
    board.find("alpha").state = "completed"
    assert any("history ends" in problem for problem in board.verify())


def test_verify_detects_broken_history(board: board_state.Board) -> None:
    item = board_state.Item(
        "alpha", "Alpha", "completed", ticket=1, owner="claude",
        history=[
            board_state.Change("2026-01-01T00:00:00+00:00", "backlog", "claude"),
            board_state.Change("2026-01-02T00:00:00+00:00", "completed", "claude",
                          frm="ready_for_review"),
        ])
    board.items.append(item)
    problems = board.verify()
    assert any("chain is broken" in problem for problem in problems)


def test_verify_detects_illegal_recorded_transition(board: board_state.Board) -> None:
    item = board_state.Item(
        "alpha", "Alpha", "completed", ticket=1, owner="claude",
        history=[
            board_state.Change("2026-01-01T00:00:00+00:00", "backlog", "claude"),
            board_state.Change("2026-01-02T00:00:00+00:00", "completed", "claude",
                          frm="backlog"),
        ])
    board.items.append(item)
    assert any("permission table forbids" in problem for problem in board.verify())
