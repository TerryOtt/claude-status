"""Loopback REST contract tests using the real threaded handler."""

import json
import pathlib
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from conftest import USERS

import serve
import status


@dataclass(frozen=True)
class RunningApi:
    base: str
    path: pathlib.Path


@pytest.fixture
def api(tmp_path: pathlib.Path) -> Iterator[RunningApi]:
    """Run the production handler against an isolated board."""
    path = tmp_path / "board.json"
    board = status.Board(project="API", users=USERS, browser_user="terry",
                         cli_user="claude", default_owner="claude")
    status.save(board, path)
    serve.BOARD_PATH = path
    serve.STORE = serve.BoardStore(path)
    server = serve.http.server.ThreadingHTTPServer((serve.HOST, 0), serve.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield RunningApi(f"http://127.0.0.1:{server.server_address[1]}", path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        serve.STORE = None


def request_json(url: str, *, token: str | None = None,
                 revision: int | None = None,
                 body: dict[str, object] | None = None) -> tuple[int, dict[str, Any]]:
    """Return status and decoded JSON for both success and HTTP refusal."""
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if revision is not None:
        headers["If-Match"] = f'"revision-{revision}"'
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if body is not None else "GET")
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
        except ConnectionAbortedError:
            if attempt:
                raise
            time.sleep(0.02)
    raise AssertionError("request retry fell through")


def assert_http_error(request: urllib.request.Request, expected: int) -> None:
    """Assert an HTTP refusal, retrying one transient Windows socket abort."""
    for attempt in range(2):
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == expected
            return
        except ConnectionAbortedError:
            if attempt:
                raise
            time.sleep(0.02)
    raise AssertionError("request retry fell through")


def test_board_and_status_reads(api: RunningApi) -> None:
    status_code, board = request_json(api.base + "/v1/board")
    health_code, health = request_json(api.base + "/v1/status")
    assert status_code == 200 and board["revision"] == 0
    assert health_code == 200 and health["ok"] is True


def test_authenticated_create_uses_browser_actor(api: RunningApi) -> None:
    code, response = request_json(
        api.base + "/v1/cards", token=serve.BROWSER_TOKEN, revision=0,
        body={"subject": "Alpha", "state": "backlog"})
    saved = status.load(api.path)
    assert code == 200
    assert response["revision"] == 1
    assert saved.find("1").history[0].by == "terry"


def test_missing_credential_returns_401(api: RunningApi) -> None:
    code, response = request_json(
        api.base + "/v1/cards", revision=0,
        body={"subject": "Alpha", "state": "backlog"})
    assert code == 401
    assert "credential" in response["error"]


def test_missing_revision_returns_428(api: RunningApi) -> None:
    code, response = request_json(
        api.base + "/v1/cards", token=serve.BROWSER_TOKEN,
        body={"subject": "Alpha", "state": "backlog"})
    assert code == 428
    assert "If-Match" in response["error"]


def test_stale_revision_returns_412_without_write(api: RunningApi) -> None:
    request_json(api.base + "/v1/cards", token=serve.BROWSER_TOKEN, revision=0,
                 body={"subject": "Alpha", "state": "backlog"})
    code, response = request_json(
        api.base + "/v1/cards/1/comment", token=serve.BROWSER_TOKEN, revision=0,
        body={"text": "stale"})
    saved = status.load(api.path)
    assert code == 412
    assert "refresh" in response["error"]
    assert saved.revision == 1
    assert saved.find("1").comments == []


def test_domain_refusal_returns_409(api: RunningApi) -> None:
    code, response = request_json(
        api.base + "/v1/cards", token=serve.CLI_TOKEN, revision=0,
        body={"id": "alpha", "subject": "Alpha", "state": "completed"})
    assert code == 409
    assert "may not create" in response["error"]
    assert status.load(api.path).revision == 0


def test_malformed_json_returns_400(api: RunningApi) -> None:
    request = urllib.request.Request(
        api.base + "/v1/cards", data=b"{", method="POST",
        headers={"Authorization": f"Bearer {serve.BROWSER_TOKEN}",
                 "Content-Type": "application/json",
                 "If-Match": '"revision-0"'})
    assert_http_error(request, 400)
    assert status.load(api.path).revision == 0


def test_unknown_route_returns_404(api: RunningApi) -> None:
    request = urllib.request.Request(api.base + "/v1/unknown", data=b"{}", method="POST")
    assert_http_error(request, 404)
