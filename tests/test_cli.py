"""CLI-to-service boundary tests."""

import pathlib
import subprocess
import sys
import threading
from typing import TYPE_CHECKING

import pytest

from localswim import api_endpoint, board_state
from tests.support import USERS

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def served_board(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    """Publish the production rendezvous file for an isolated service."""
    path = tmp_path / "board.json"
    board = board_state.Board(
        project="CLI", users=USERS, browser_user="terry", cli_user="bot", default_owner="bot"
    )
    board_state.save(board, path)
    api_endpoint.BOARD_PATH = path
    api_endpoint.STORE = api_endpoint.BoardStore(path)
    server = api_endpoint.http.server.ThreadingHTTPServer(
        (api_endpoint.HOST, 0), api_endpoint.Handler
    )
    api_endpoint.publish_service(path, server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        api_endpoint.remove_service(path)
        api_endpoint.STORE = None


def run_cli(path: pathlib.Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Invoke the real command-line entry point."""
    return subprocess.run(
        [sys.executable, "-m", "localswim.board_state", str(path), *arguments],
        cwd=pathlib.Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def assert_cli(path: pathlib.Path, *arguments: str) -> None:
    """Require one CLI command to succeed."""
    result = run_cli(path, *arguments)
    assert result.returncode == 0, result.stderr


def test_every_cli_mutation_uses_service(served_board: pathlib.Path) -> None:
    assert_cli(served_board, "--create", "a", "Alpha", "--state", "backlog")
    assert_cli(served_board, "--create", "b", "Beta", "--state", "backlog")
    assert_cli(served_board, "--comment", "a", "hello")
    assert_cli(served_board, "--assign", "a", "terry")
    assert_cli(served_board, "--set-priority", "a", "P1")
    assert_cli(served_board, "--set-detail", "a", "--detail", "description")
    assert_cli(served_board, "--set-subject", "a", "Renamed")
    assert_cli(served_board, "--link", "a", "relates_to", "b")
    assert_cli(served_board, "--set-parent", "b", "a")
    assert_cli(served_board, "--set-project", "Updated")
    assert_cli(served_board, "--unlink", "a", "relates_to", "b")
    assert_cli(served_board, "--clear-parent", "b")

    board = board_state.load(served_board)
    item = board.find("a")
    assert board.revision == 12
    assert board.project == "Updated"
    assert item.subject == "Renamed"
    assert item.detail == "description"
    assert item.owner == "terry"
    assert item.priority == "P1"
    assert item.comments[0].by == "bot"
    assert board.links == []
    assert board.find("b").parent is None


def test_offline_mutation_has_no_direct_write(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    board = board_state.Board(
        project="Offline",
        users=USERS,
        browser_user="terry",
        cli_user="bot",
        default_owner="bot",
    )
    board_state.save(board, path)
    board_state.service_descriptor_path(path).unlink(missing_ok=True)

    result = run_cli(path, "--create", "alpha", "Alpha", "--state", "backlog")
    saved = board_state.load(path)
    assert result.returncode != 0
    assert "service is not running" in result.stderr
    assert "Traceback" not in result.stderr
    assert saved.revision == 0
    assert saved.items == []


def test_read_only_report_works_offline(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    board = board_state.Board(
        project="Offline report",
        users=USERS,
        browser_user="terry",
        cli_user="bot",
        default_owner="bot",
    )
    board_state.save(board, path)
    result = run_cli(path)
    assert result.returncode == 0
    assert "Offline report" in result.stdout
