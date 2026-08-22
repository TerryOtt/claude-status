# localswim

A small, local swimlane board for one human and one automation agent. It serves a
draggable browser UI over a single JSON file, binds only to loopback, and has no runtime
dependencies outside Python's standard library.

## Quick start

Requirements:

- [uv](https://docs.astral.sh/uv/) 0.12.5 (it installs the pinned Python 3.14.7)
- A modern browser
- Git only if you want build identifiers or opt-in automatic pushes

Clone the repository, create a private board directory, and copy the example:

~~~text
boards/
└── my-project.json    <- copy of examples/board.example.json
~~~

Then start the service:

~~~console
uv python install
uv sync --locked
uv run --frozen localswim boards/my-project.json
~~~

Open the URL it prints, normally **http://127.0.0.1:8792/**. The port comes from the
board file so different projects can have stable bookmarks. The --port option provides
a temporary override.

The boards/ directory is ignored by Git and is the only permitted location for board
files kept inside this public checkout. A board may instead live in a separate
repository or directory.

## Board configuration

The checked [example board](examples/board.example.json) is valid, empty and ready to
run with the repository's default terry and claude permission actors.

The important fields are:

| Field | Meaning |
|---|---|
| schema | Board format version; currently 2. |
| project | Name shown in the browser. |
| port | Loopback TCP port; defaults to 8792 if omitted. |
| users | Valid identities, labels, classes (human or bot), and UI colors. |
| browserUser | Identity used for browser changes. |
| cliUser | Identity used for CLI changes. |
| defaultOwner | Owner assigned when a new card does not specify one. |
| revision | Monotonic write version; start a new board at 0. |
| nextTicket | Next display number; start an empty board at 1. |
| items | Cards; start an empty board with an empty list. |

User IDs are case-sensitive. The two transition actors in rules.json MUST exactly match
browserUser and cliUser. To rename the actors, update both files together. Malformed
JSON and invalid fields fail with a path, line or field-specific explanation.

## Daily use

The browser supports creating, moving, editing, assigning, prioritizing and relating
cards. The CLI can inspect the snapshot while the service is stopped or running:

~~~console
uv run --frozen localswim-board boards/my-project.json
uv run --frozen localswim-board boards/my-project.json --verify
uv run --frozen localswim-board boards/my-project.json --json
~~~

CLI mutations use the running REST service so browser and CLI writes share validation,
locking and revision checks:

~~~console
uv run --frozen localswim-board boards/my-project.json --create docs "Write setup docs" --state ready_for_claude
uv run --frozen localswim-board boards/my-project.json --comment docs "First draft is ready"
uv run --frozen localswim-board boards/my-project.json --move docs in_progress
~~~

Run **uv run --frozen localswim-board --help** for the complete command list. CLI changes are
attributed to cliUser; browser changes are attributed to browserUser. Request bodies
cannot choose another identity.

## Transition permissions

rules.json is the allow-list for lanes, priorities, card creation and movement. Edges
are grouped under exactly two actor IDs:

~~~json
"edges": {
  "terry": [
    {
      "from": "backlog",
      "to": "ready_for_claude",
      "description": "Why Terry may make this move."
    }
  ],
  "claude": [
    {
      "from": "ready_for_claude",
      "to": "in_progress"
    }
  ]
}
~~~

Each edge MUST contain string from and to lane IDs, MAY contain a string description,
and MUST contain no other fields. Unknown lanes, self-loops, duplicate actor edges and
duplicate JSON keys are rejected. Valid rules.json edits reload live.

## Data safety and Git

`localswim.api_endpoint` is the sole writer while a board is live. Every mutation
validates the model, increments revision, flushes a temporary file in the board's
directory and atomically replaces the previous snapshot. Stale writes receive HTTP 412
instead of overwriting newer state.

Board JSON contains identities, descriptions, comments and audit history. Keep it out
of this public source repository.

Automatic Git commits and pushes are OFF by default. Enable them only for a board stored
in an appropriate Git repository:

~~~console
uv run --frozen localswim --autopush path/to/board.json
~~~

When enabled, the server commits only the board path after five quiet seconds and pushes
the board repository's current branch. It refuses ignored boards, non-repositories and
repositories without a remote; it cannot prove that a configured remote is private.

## REST API

The browser and CLI use these loopback routes:

~~~text
GET  /api/v001/status
GET  /api/v001/board
POST /api/v001/cards
POST /api/v001/cards/<id>/{move,comment,assign,priority,subject,detail,link,parent}
POST /api/v001/board/project
~~~

Only the `/api/v001` routes are API endpoints. For seamless upgrades of already-open
tabs, `/v1/status` and `/mtime` issue no-cache redirects to
`/api/v001/status`. Earlier board and mutation routes are not retained.

Mutations require the per-process bearer credential and an
If-Match: "revision-N" header. Credentials are published in a user-local temporary
service descriptor; they are not stored in the board.

## Live code updates

The server watches its installed `api_endpoint` and `board_state` modules. A valid
change is debounced, preflighted in a child Python process and applied by gracefully
re-executing the server. An invalid change leaves the healthy process running and
reports the preflight error.

Open tabs detect the new build and reload once. Comment drafts, active editors, a
partially written card, search text and view state survive in per-tab session storage.

## Development

Before changing the implementation, read the
[contributor orientation](docs/ORIENTATION.md). It maps the components, data and
request flows, schemas, safety invariants, state-policy caveats, test boundaries, and
Windows/Codex environment details that are intentionally more technical than this user
guide. The branch and pull-request policy lives in
[CONTRIBUTING.md](CONTRIBUTING.md), and automation-specific working rules live in
[AGENTS.md](AGENTS.md). The reasoning and pins behind the development tools are in
[docs/TOOLING.md](docs/TOOLING.md).

Install the exact Python and locked development environment:

~~~console
uv python install
uv sync --locked
~~~

Run the complete gate:

~~~console
uv run --frozen python check.py
~~~

The gate runs LF line-ending validation, Ruff `ALL`, Ruff formatting, strict Pyright,
pytest, actionlint with ShellCheck, and the project's US English vocabulary check. The
vocabulary table comes from the public FlickrGroupAddr/backend-api repository; pass its
path with **--word-table path/to/claude-dirty-words.py** if it is not in a neighboring
checkout. actionlint 1.7.12 and ShellCheck 0.11.0 must be on `PATH`; installation details
are in [docs/TOOLING.md](docs/TOOLING.md).

Build reproducible source and wheel distributions with the declared backend, ignoring
any local uv dependency-source overrides:

~~~console
uv build --no-sources --clear
~~~

Enable the local pre-commit hook once per clone:

~~~console
git config core.hooksPath .githooks
~~~

GitHub Actions runs the same gate on pushes and pull requests. Text files are enforced
as UTF-8 with LF endings by .editorconfig, .gitattributes and the gate.

## License

MIT. See LICENSE.
