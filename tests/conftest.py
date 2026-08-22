"""Shared, isolated board fixtures."""

from typing import TYPE_CHECKING

import pytest

from localswim import board_state
from tests.support import USERS

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def configured_users() -> Iterator[None]:
    """Restore the module-level cast before and after every test."""
    board_state.install_users(USERS, "terry", "bot", "bot", "pytest")
    yield
    board_state.install_users(USERS, "terry", "bot", "bot", "pytest cleanup")


@pytest.fixture
def board() -> board_state.Board:
    """An empty, valid board using the standard two test actors."""
    return board_state.Board(
        project="Test", users=USERS, browser_user="terry", cli_user="bot", default_owner="bot"
    )
