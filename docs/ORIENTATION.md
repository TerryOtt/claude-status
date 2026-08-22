# Contributor orientation

This document is the technical map for humans and coding agents entering localswim.
`README.md` is the user guide, `AGENTS.md` is the working agreement for automation,
and the long module docstrings preserve the detailed decision history. This file
connects those pieces without requiring a new contributor to rediscover the system by
reading nearly 7,000 lines in an arbitrary order.

## The system in one page

localswim is one Python process serving one local board from one JSON snapshot. It has
no runtime dependency outside Python's standard library and makes no browser network
request beyond loopback.

```text
browser UI ── bearer token + If-Match ─┐
                                       ├──> ThreadingHTTPServer
CLI mutation ── service descriptor ────┘          │
                                                  v
                                             BoardStore
                                                  │
                                  RLock + file lock + validation
                                                  │
                                                  v
                                    temporary JSON + fsync + replace
                                                  │
                                                  v
                                             board.json

CLI report ───────────────────────────────> validated read of board.json
rules.json ───────────────────────────────> immutable TransitionPolicy
```

The important boundary is `BoardStore.execute()`: every live mutation is serialized,
checked against the revision the caller saw, applied to an isolated candidate, audited,
saved, and only then published as the current in-memory snapshot.

## Repository map

| Path | Responsibility |
|---|---|
| `board_state.py` | Domain model, policy loading, validation, history replay, relationships, JSON storage, file locking, and CLI. |
| `api_endpoint.py` | Loopback HTTP server, transactional store, REST boundary, embedded HTML/CSS/JavaScript, live reload, and optional Git publishing. |
| `rules.json` | Tool-level lanes, priorities, creation rights, and actor-grouped transition edges. |
| `examples/board.example.json` | Empty, valid per-project board configuration. |
| `check.py` | Complete local/CI gate: line endings, Ruff, Pyright, pytest, and US English vocabulary. |
| `tests/` | Domain, policy, serialization, store, API, CLI, docs, and gate behavior. |
| `vendor/typefaces/inter/` | Unmodified Inter WOFF2 subsets and their SIL OFL license. |
| `.github/workflows/gate.yml` | Runs the same gate in CI and obtains the public canonical vocabulary table. |
| `.github/workflows/contribution-policy.yml` | Checks non-exempt pull-request source branch names without executing PR code. |
| `.github/rulesets/main.json` | Reproducible recipe for the server-side `main` branch ruleset. |
| `.githooks/pre-commit` | Opt-in local hook that executes `python check.py`. |

There is no package metadata, JavaScript build, template engine, database service, or
dependency lock file. Run both Python entry points from the checkout so sibling imports
and development tools resolve consistently.

## Configuration and schema ownership

Three JSON shapes have separate version numbers because they change independently:

| Data | Schema | Owner and lifetime |
|---|---:|---|
| Board snapshot | 2 | Per project, durable, selected on the command line. |
| Transition rules | 6 | Per tool checkout, durable, loaded from `rules.json`. |
| Service descriptor | 1 | Per running process, temporary, stored outside the board. |

The board owns deployment-specific data:

- project name and loopback port;
- users, labels, human/bot classes, and UI colors;
- `browserUser`, `cliUser`, and `defaultOwner`;
- monotonic `revision` and `nextTicket` values;
- cards, links, comments, and history.

The rules own tool behavior:

- lane order, IDs, labels, and lane creation rights;
- priority order, IDs, labels, and default;
- directed transition edges grouped under actor IDs.

`browserUser` and `cliUser` must be different. The rules must contain exactly two edge
actors, and those IDs must exactly match the configured browser and CLI users. A board
may list additional users as owners, but only the browser and CLI identities are
authenticated mutation actors.

User and policy IDs join case-sensitively. Keep IDs lower-case: request values passed
through `as_actor()` are trimmed and lowercased before matching, while configured IDs
and policy keys are otherwise exact.

JSON parsing rejects duplicate object keys globally and reports parser line/column
locations. Validation is deliberately strict about required types, references,
identifiers, counters, and rule-edge fields. Do not assume every object rejects every
unknown key: rule edges explicitly do; the board loader currently tolerates additional
keys in several persisted objects.

## Cards, identity, and audit

Every card has two permanent handles:

- `id` is a stable machine slug used by links and API paths. Renaming a title does not
  change it.
- `ticket` is a monotonically allocated human reference rendered as `#0001`. Deleted
  or archived numbers must never be reused.

`nextTicket` is stored rather than derived and must remain above every existing ticket.
Lane display order is total: policy priority, creation time from history, then ticket.
Cards without a creation event sort as the oldest because they predate that mechanism.

History and comments are separate. History is machine-written and append-only;
comments are human/bot prose. There are three history event shapes:

| Kind | Discriminant | Meaning |
|---|---|---|
| Lane | neither `ownerTo` nor `priorityTo` | Creation (`from` absent) or movement. |
| Owner | `ownerTo` present | Reassignment; ownership is a label, not authorization. |
| Priority | `priorityTo` present | Priority change. |

`Board.verify()` replays lane history, checks legal actors and chain continuity, and
compares the result to stored state. It also validates owner and priority targets in
their history entries. It detects direct state edits but cannot prove that a hand-edited
history entry naming an actor on a legal edge was really made by that actor.

Title, description, project, parent, and relationship edits do not receive their own
board-history entries. The existing design expects Git history to carry those earlier
values when the board lives in a Git repository. Automatic Git publishing is off by
default, so that expectation is only true when the operator has deliberately arranged
versioned board storage.

## Relationships and hierarchy

Parent/child hierarchy is separate from general relationships because a tree needs one
parent per card and must reject cycles. `parent` stores the parent's stable card ID.

General links are stored once in canonical direction and their inverse is derived:

| Stored | Derived inverse |
|---|---|
| `blocks` | `blocked_by` |
| `duplicates` | `duplicated_by` |
| `references` | `referenced_by` |
| `relates_to` | `relates_to` |

This representation makes a half-written relationship impossible. Human comments
automatically add `references` links for `#1234` mentions; bot comments intentionally
do not, because bots commonly mention ticket numbers while explaining rather than
linking. Explicit CLI/API links remain available to either authenticated actor.

## Lane policy and the non-table rule

`rules.json` is the executable allow-list. The current lane meanings are:

| Lane | Meaning |
|---|---|
| `backlog` | Someday queue controlled by the human. |
| `ready_for_claude` | Work selected for the automation agent. Display label: Ready For Work. |
| `in_progress` | Work actively being performed. |
| `blocked` | Nobody can act, such as waiting for an external license key. |
| `needs_terry_action` | The human must answer, decide, or personally act. |
| `ready_for_review` | Automation believes the work is done and awaits signoff. |
| `completed` | Terminal, append-only lane. |

Two constraints matter beyond a simple `may_move()` lookup:

1. The automation actor has a `backlog -> ready_for_claude` edge only so the human can
   authorize that move for one named ticket. It must never use the edge unsolicited.
2. Automation cannot sign off its own work. Only the browser/human actor has
   `ready_for_review -> completed`.

Do not use owner as a second permission system. Either actor can move or reassign a
card regardless of its owner; transition policy alone grants movement.

## Persistence and concurrency

The storage sequence is designed so a reader sees the old complete snapshot or the new
complete snapshot:

1. Create `board.json.lock` with `O_CREAT | O_EXCL`.
2. Load and validate while holding the lock.
3. Mutate and verify the candidate.
4. Increment the revision for a service mutation.
5. Serialize indented UTF-8 JSON with a trailing LF to a same-directory PID temp file.
6. Flush and `fsync` the temp file.
7. Atomically replace the target.
8. Remove the lock.

Lock acquisition waits up to one second. A lock older than ten seconds is considered
abandoned and may be removed. Atomic replacement retries only transient Windows
`PermissionError` failures: six attempts, 40 ms apart. Other I/O failures surface and
the prior snapshot remains in place.

The server adds an in-process `RLock` around the filesystem lock. Two clients writing
the same revision produce one winner and one HTTP 412 response; a stale command never
overwrites newer state.

## HTTP and CLI boundaries

The server binds only to `127.0.0.1`. Read routes are loopback-accessible without a
credential. Mutations require both a per-process bearer credential and
`If-Match: "revision-N"`.

Current API routes:

```text
GET  /api/v001/status
GET  /api/v001/board
POST /api/v001/cards
POST /api/v001/cards/<id>/{move,comment,assign,priority,subject,detail,link,parent}
POST /api/v001/board/project
```

The server maps credentials to actors; request bodies cannot select who is acting. The
browser credential is embedded in the served page. The CLI credential is published in
a temporary service descriptor whose filename is a hash of the resolved, case-folded
board path. The descriptor lives under the system temp `claude-status` directory and
is removed only by the process that owns its token.

Useful refusal statuses are:

| Status | Meaning |
|---:|---|
| 400 | Malformed body or missing request field. |
| 401 | Missing or invalid process credential. |
| 404 | Unknown or retired route. |
| 409 | Domain, policy, validation, or storage refusal. |
| 412 | Revision is stale. Refresh and retry deliberately. |
| 428 | Missing or malformed `If-Match`. |

Only `/v1/status` and `/mtime` remain as no-cache redirects so an old tab can discover
the current build. Earlier board and mutation routes intentionally return 404.

CLI reports load the board directly and work while the service is stopped. Public CLI
mutations do not write the file directly: they locate the running service, fetch its
revision, and use the same REST mutation path as the browser. CLI mutations are always
attributed to `cliUser`; there is no impersonation flag.

## Board-data placement

A board contains identities, prose, comments, and audit history. Inside this public
source checkout it is accepted only beneath `boards/`, and only while Git confirms that
directory is ignored. Boards elsewhere remain supported and are the normal choice for
versioned/private project data.

Do not use a source-tree fixture path to test the "outside checkout" behavior: that
changes the premise of the safety check. The Codex test command therefore uses
`C:\Temp`, as documented in `AGENTS.md`.

## Browser UI behavior

The browser application is a single embedded `PAGE` string in `api_endpoint.py`. There
is no separate asset build. Inter's Latin and Latin Extended subsets are served locally
with immutable caching; all board/page responses are no-store.

Important UI mechanics:

- Poll `/api/v001/status` every 400 ms and fetch the board only when its mtime changes.
- Treat a successful parse, not merely an HTTP response or `stat`, as proof of life.
- Repaint lanes wholesale while preserving unsent comment, editor, new-card, search,
  and view state.
- Use session storage to survive one guarded reload when the page build differs from
  the server build.
- Preflight changed Python in a child process; valid source triggers graceful server
  re-exec, while invalid source leaves the healthy process running and reports why.
- Reload `rules.json` live. A bad edit retains the last valid immutable policy and is
  reported once for that file version.
- Search titles, descriptions, comments, and tickets entirely in the browser.
- Hide completed cards after 24 hours only when the lane-entry time is known.
- Derive draggable edges, creatable lanes, labels, priorities, actors, and colors from
  server data rather than duplicating policy in JavaScript.

The build ID is the short Git commit. A dirty checkout appends a fingerprint of the two
loaded Python sources and `-dirty`; consequently, uncommitted source edits produce
distinct builds instead of sharing one generic dirty ID.

## Optional Git publishing

`--autopush` is off unless explicitly supplied. When enabled, a daemon thread waits
five quiet seconds after the latest board write, commits only the board path, and pushes
the board repository's current branch.

It refuses boards that are ignored, outside a Git repository, or in a repository with
no remote. It cannot establish that a configured remote is private. A failed push keeps
the successful local commit so a later push can carry it; the UI reports the failure.
The ignored `boards/` directory in this public source checkout is therefore for local
data, not for autopush.

## Tests and development gate

The suite currently contains 108 tests across these boundaries:

| Module | Coverage focus |
|---|---|
| `test_board.py` | Card operations, transitions, history, links, hierarchy, and sorting. |
| `test_serialization.py` | JSON validation, duplicate keys/IDs/tickets, atomic save, and locking. |
| `test_policy.py` | Policy immutability, strict edge parsing, actor matching, isolation, and reload. |
| `test_store.py` | Transactionality, revision races, board placement, and autopush startup. |
| `test_api.py` | Real threaded loopback routes, auth, revisions, redirects, and code restart. |
| `test_cli.py` | CLI-to-service mutations and offline read behavior. |
| `test_docs.py` | Checked example board. |
| `test_check.py` | Gate line-ending parser. |
| `conftest.py` | Standard isolated Terry/Claude cast and empty board fixtures. |

Use the exact Codex command from `AGENTS.md` so fixtures and pytest's cache live under
the authorized external scratch root. The complete project gate is:

```console
python check.py --word-table path/to/claude-dirty-words.py
```

The gate runs LF validation, Ruff, Pyright, pytest, and the external US English/house
vocabulary checker. Ruff verifies that annotations exist; Pyright verifies their
types. The vocabulary detector first runs a known-bad control sentence and fails if the
canonical table is missing. The checker scans Git-tracked `.py`, `.md`, and `.json`
files, so a newly created untracked document is not covered until it becomes tracked.

CI obtains the canonical word table from the public `FlickrGroupAddr/backend-api`
repository instead of copying it here. The local hook is inactive until a developer
runs `git config core.hooksPath .githooks`.

## Windows/Codex environment notes

The checkout may be owned by the interactive Windows user while commands run as the
Codex sandbox identity. No re-clone is needed. The one-time trust setting and the
per-command global-excludes override are documented in `AGENTS.md`.

The relevant rules are:

- global `safe.directory` trusts `C:/Projects/localswim`;
- `-c core.excludesFile=/dev/null` suppresses only the inaccessible user-level ignore
  file while preserving this repository's `.gitignore`;
- `C:\Temp` is the authorized external scratch root for Codex work on this machine;
- the GitHub CLI token needs `repo` and `workflow` scopes before a push may add or
  change Actions workflows;
- keep Git commands non-interactive and do not change repository configuration merely
  to compensate for sandbox-account warnings.

## Repository governance

The checked-in policy is detailed in `CONTRIBUTING.md`. GitHub's active ruleset, not
the documentation or JSON recipe by itself, is the enforcement boundary:

- `TerryOtt` (GitHub user ID `17037862`) may bypass the `main` ruleset and push
  directly.
- Local Codex uses Terry's credential and is covered by that same user bypass.
- All separately authenticated actors must reach `main` through a pull request whose
  source name matches `feature/<lower-case-kebab-description>`.
- The `contribution-policy` workflow performs that PR-source check without checking
  out or executing untrusted contributor code.

A local Codex session uses the Git credential supplied by its environment. In this
checkout that is Terry's credential, so GitHub sees local Codex pushes as `TerryOtt`.
OpenAI's `chatgpt-codex-connector` (GitHub App ID `1144995`) is not currently installed
or authorized for this repository; GitHub rejected it as a bypass actor for that
reason. Cloud Codex must therefore follow the feature-branch policy unless Terry later
installs the App and adds it to both the live rule and recipe. The branch bypass does
not override the repository instruction that Codex commits and pushes only after an
explicit user request.

The main ruleset intentionally requires zero fixed review approvals: the requested
boundary is “submit a PR,” with Terry retaining the merge decision. It requires the
branch-policy check but does not duplicate the existing gate as a required status
check. The gate still runs on every push and pull request and should be green before a
merge. If policy expectations change, update the GitHub rule, recipe, workflow,
`CONTRIBUTING.md`, and this section together.

## Documentation ownership

Keep each fact in the narrowest durable place:

- `README.md`: installation, operation, public API, and user-facing behavior.
- `CONTRIBUTING.md`: branch naming, pull requests, and GitHub ruleset maintenance.
- `docs/ORIENTATION.md`: contributor-level component map, data flow, invariants, and
  known sharp edges.
- `AGENTS.md`: concise rules an automation agent must follow while changing the repo.
- `rules.json` notes/descriptions: why a specific lane or actor edge exists.
- module/class/function docstrings: detailed implementation decisions and measured
  failure history closest to the code they constrain.
- tests: executable contracts.
- `vendor/typefaces/inter/README.md` and `LICENSE.txt`: font provenance and license.

When a schema, route, invariant, or workflow changes, update every affected layer in
the same change. Do not copy the full transition table or vocabulary list into another
document; link to the authoritative data so it cannot drift.
