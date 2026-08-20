"""Shared, isolated board fixtures."""

from collections.abc import Iterator

import pytest

import status

USERS = (
    status.User("terry", "Terry", status.HUMAN, "#2266aa"),
    status.User("claude", "Claude", status.BOT, "#aa6622"),
)


@pytest.fixture(autouse=True)
def configured_users() -> Iterator[None]:
    """Restore the module-level cast before and after every test."""
    status.install_users(USERS, "terry", "claude", "claude", "pytest")
    yield
    status.install_users(USERS, "terry", "claude", "claude", "pytest cleanup")


@pytest.fixture
def board() -> status.Board:
    """An empty, valid board using the standard two test actors."""
    return status.Board(project="Test", users=USERS, browser_user="terry",
                        cli_user="claude", default_owner="claude")
