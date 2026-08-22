"""Typed constants shared by the behavioral tests and fixtures."""

from localswim import board_state

USERS = (
    board_state.User("terry", "Terry", board_state.HUMAN, "#2266aa"),
    board_state.User("claude", "Claude", board_state.BOT, "#aa6622"),
)
