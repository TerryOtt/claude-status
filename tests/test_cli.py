"""CLI-to-service boundary tests."""

import pathlib
import subprocess
import sys
import threading
from collections.abc import Iterator

import pytest
from conftest import USERS

import serve
import status


@pytest.fixture
def served_board(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    """Publish the production rendezvous file for an isolated service."""
    path = tmp_path / "board.json"
    board = status.Board(project="CLI", users=USERS, browser_user="terry",
                         cli_user="claude", default_owner="claude")
    status.save(board, path)
    serve.BOARD_PATH = path
    serve.STORE = serve.BoardStore(path)
    server = serve.http.server.ThreadingHTTPServer((serve.HOST, 0), serve.Handler)
    serve.publish_service(path, server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        serve.remove_service(path)
        serve.STORE = None


def run_cli(path: pathlib.Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Invoke the real command-line entry point."""
    return subprocess.run(
        [sys.executable, "status.py", str(path), *arguments],
        cwd=pathlib.Path(__file__).resolve().parents[1], capture_output=True,
        text=True, timeout=10, check=False)


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

    board = status.load(served_board)
    item = board.find("a")
    assert board.revision == 12
    assert board.project == "Updated"
    assert item.subject == "Renamed" and item.detail == "description"
    assert item.owner == "terry" and item.priority == "P1"
    assert item.comments[0].by == "claude"
    assert board.links == [] and board.find("b").parent is None


def test_offline_mutation_has_no_direct_write(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    board = status.Board(project="Offline", users=USERS, browser_user="terry",
                         cli_user="claude", default_owner="claude")
    status.save(board, path)
    status.service_descriptor_path(path).unlink(missing_ok=True)

    result = run_cli(path, "--create", "alpha", "Alpha", "--state", "backlog")
    saved = status.load(path)
    assert result.returncode != 0
    assert "service is not running" in result.stderr
    assert "Traceback" not in result.stderr
    assert saved.revision == 0 and saved.items == []


def test_read_only_report_works_offline(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    board = status.Board(project="Offline report", users=USERS,
                         browser_user="terry", cli_user="claude",
                         default_owner="claude")
    status.save(board, path)
    result = run_cli(path)
    assert result.returncode == 0
    assert "Offline report" in result.stdout
