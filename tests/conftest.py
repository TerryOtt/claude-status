"""Shared, isolated board fixtures."""

from collections.abc import Iterator

import pytest

import board_state

USERS = (
    board_state.User("terry", "Terry", board_state.HUMAN, "#2266aa"),
    board_state.User("claude", "Claude", board_state.BOT, "#aa6622"),
)


@pytest.fixture(autouse=True)
def configured_users() -> Iterator[None]:
    """Restore the module-level cast before and after every test."""
    board_state.install_users(USERS, "terry", "claude", "claude", "pytest")
    yield
    board_state.install_users(USERS, "terry", "claude", "claude", "pytest cleanup")


@pytest.fixture
def board() -> board_state.Board:
    """An empty, valid board using the standard two test actors."""
    return board_state.Board(project="Test", users=USERS, browser_user="terry",
                        cli_user="claude", default_owner="claude")
