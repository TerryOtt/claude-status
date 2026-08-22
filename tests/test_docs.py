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


def test_initialization_examples_regenerate_checked_board(tmp_path: pathlib.Path) -> None:
    root = pathlib.Path(__file__).resolve().parents[1]
    generated = tmp_path / "board.json"

    board_state.initialize_board(
        generated,
        root / "examples" / "board-description.example.json",
        root / "examples" / "permissions.example.json",
    )

    checked = board_state.load(root / "examples" / "board.example.json")
    assert board_state.load(generated).to_json() == checked.to_json()
