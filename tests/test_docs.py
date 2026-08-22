"""Executable documentation examples."""

import pathlib

from localswim import board_state


def test_example_board_is_valid_and_empty() -> None:
    example = pathlib.Path(__file__).resolve().parents[1] / "examples" / "board.example.json"

    board = board_state.load(example)

    assert board.project == "Example project"
    assert board.port == 8792
    assert board.items == []
    assert board.browser_user == "terry"
    assert board.cli_user == "bot"
