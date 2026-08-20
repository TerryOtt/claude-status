# claude-status
Claude-Terry swimlane task tracker

A live, draggable board over one board JSON, served on loopback.

```
python api_endpoint.py boards/<project>.json
```

Real board files kept inside this source checkout MUST live under `boards/`. That
directory is explicitly ignored because a board contains identities, descriptions,
comments and audit history. `api_endpoint.py` refuses to open a board elsewhere inside this
public checkout, even if somebody later removes or weakens the ignore rule. Boards in
separate repositories remain supported as an intentional deployment choice.

The port comes from the board file's own `port` field rather than a flag, so one
bookmark per project cannot open the wrong board. `board_state.py` is the library and the
command line; `rules.json` is the permission model and is re-read live.

## Persistence and the REST API

`api_endpoint.py` is the sole writer while a board is live. The browser and `board_state.py` CLI
send domain commands to its loopback REST API; neither submits a replacement board.
Each successful command validates the model, increments the board's monotonic
`revision`, flushes a temporary JSON file and atomically replaces the prior snapshot.

Mutation requests use `If-Match: "revision-N"`. A stale client receives HTTP 412 and
must refresh rather than overwrite a newer command. Bearer credentials are published
in a user-local temporary rendezvous file: the browser credential maps to the board's
`browserUser`, and the CLI credential maps to `cliUser`. Request bodies cannot choose
their actor.

The primary routes are:

```
GET  /v1/status
GET  /v1/board
POST /v1/cards
POST /v1/cards/<id>/{move,comment,assign,priority,subject,detail,link,parent}
POST /v1/board/project
```

CLI reports remain available directly from the JSON snapshot. CLI mutations require
the board service to be running and fail without changing anything when it is absent;
there is deliberately no direct-write fallback.

## The gate

```
python check.py
```

Runs `ruff`, `pyright`, the `pytest` behavioral suite and the US English /
house-vocabulary check over every tracked `.py`, `.md` and `.json`. **Run it before
committing.**

The runtime remains standard-library-only. Install the development tools once with:

```
python -m pip install ruff pyright pytest
```

### Turn on the pre-commit hook. It is one command and a fresh clone skips it

```
git config core.hooksPath .githooks
```

**Until that is set the hook does not run, and nothing says so.** That is the weak link
in the whole arrangement, which is why it sits at the top of this section rather than
further down: a gate that silently does not run is worse than no gate, because it reads
as a pass.

**GitHub Actions covers the case where somebody forgets.** `.github/workflows/gate.yml`
runs the identical `check.py` on every push, on a machine nobody configured. **The two
mechanisms fail in opposite directions, which is why there are two.**

### The word table is borrowed, not copied

`check.py` imports `hits_in` from **`FlickrGroupAddr/backend-api`'s
`scripts/claude-dirty-words.py`**, which is the canonical list. A second copy would
drift, and the list is the whole tool.

It is looked for in this order:

1. `--word-table <path>`
2. the `CLAUDE_WORD_TABLE` environment variable
3. a few known checkout locations

**If it is found nowhere the gate FAILS and prints a banner saying nothing was
checked.** It is never skipped quietly -- a checker that did not run has checked
nothing, and that must not read like a clean result.
