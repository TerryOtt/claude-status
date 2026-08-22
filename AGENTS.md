# Repository guidance

## Purpose and layout

localswim is a dependency-free Python 3.13 application that serves a local swimlane
board from one JSON snapshot.

- `board_state.py` owns the board model, validation, serialization, policy checks,
  storage helpers, and command-line client.
- `api_endpoint.py` owns the loopback HTTP service and its embedded browser UI. It is
  the sole writer while a board is live.
- `rules.json` is the schema-validated allow-list for lanes, priorities, creation, and
  actor-specific transitions.
- `examples/board.example.json` is the checked, empty schema-2 example board.
- `docs/ORIENTATION.md` is the contributor map for architecture, data flow, schemas,
  invariants, test coverage, and Windows/Codex environment details.
- `CONTRIBUTING.md` defines the enforced GitHub branch and pull-request policy.
- `tests/` is the behavioral suite; `check.py` is the complete development gate.
- `vendor/typefaces/inter/` contains unmodified, licensed font binaries. Do not edit,
  re-subset, or rename them casually.

## Safety and product invariants

- Keep the service bound to loopback. Do not add remote exposure, external runtime
  services, or browser network dependencies without an explicit product decision.
- Preserve the single-writer design, per-process bearer credential, `If-Match`
  revision checks, schema validation, monotonic revisions, flushed temporary writes,
  and atomic snapshot replacement.
- Board files can contain private identities, descriptions, comments, and history.
  Keep local boards under ignored `boards/` or outside this public checkout; never add
  real board data, credentials, or service descriptors to source control.
- Automatic commits and pushes are opt-in. Do not enable `--autopush`, configure
  remotes, commit, or push unless the user explicitly asks.
- Treat `rules.json` as the permission source, not as UI decoration. Keep user IDs and
  `browserUser`/`cliUser` aligned with its actor keys.
- An automation agent must not promote a backlog card to `ready_for_claude` without
  explicit permission for that specific card, even though the edge exists for that
  exceptional case. It must never move its own work to `completed`.

## Code conventions

- Target Python 3.13 and the standard library. A new runtime dependency requires clear
  justification and an explicit decision.
- Fully annotate every function and method signature. Ruff checks annotation presence;
  Pyright checks correctness.
- Follow `ruff.toml`: 100-column lines, modern syntax, sorted imports, `pathlib` for
  paths, and the enabled lint families. Do not weaken checks to make a change pass.
- Preserve UTF-8, LF endings, and the repository's US English/house vocabulary.
- Maintain the existing JSON boundary behavior: duplicate keys, invalid types, bad
  references, illegal transitions, and malformed input should fail with useful,
  field-specific messages. Preserve unknown-field rejection where a structure defines
  it explicitly, especially transition edge objects; do not claim every board object
  currently rejects additional keys.
- Keep protocol and schema changes explicit. Update implementation, rules/example
  data, tests, and README together when a public route or persisted shape changes.
- Preserve compatibility routes only where the code intentionally documents them;
  do not invent broad legacy aliases.
- Treat `README.md`, `docs/ORIENTATION.md`, `AGENTS.md`, `rules.json` descriptions,
  implementation docstrings, and tests as complementary documentation layers. Update
  every affected layer when a schema, route, invariant, or workflow changes.

## Verification

Run focused tests while iterating, then run the complete gate before handing off a
substantial change:

```console
python -m pytest -q
python check.py --word-table path/to/claude-dirty-words.py
```

`check.py` validates tracked-file line endings, Ruff, Pyright, pytest, and the borrowed
US English vocabulary table. The word table is intentionally not copied here; its
absence is a gate failure, not a skipped check. If it is available through the
`CLAUDE_WORD_TABLE` environment variable or a documented neighboring checkout,
`python check.py` is sufficient.

For a narrow change, run the relevant test module first, but do not describe Ruff alone
as type-checking or pytest alone as the complete gate.

On this Windows machine, Codex is authorized to use `C:\Temp` for scratch data. The
inherited user temp directory is not readable by the sandbox account, so run pytest
with an external project-specific base temp:

```console
python -m pytest -q --basetemp C:/Temp/localswim-codex-pytest -o cache_dir=C:/Temp/localswim-codex-pytest-cache
```

Do not put `--basetemp` inside this checkout: the suite intentionally verifies that
boards outside the source checkout remain supported, so an in-repository temp root
changes that test's premise. Writing to `C:\Temp` may still require the normal Codex
sandbox approval even though the project author has authorized its use.

## Git in the Codex sandbox

The workspace may be owned by the interactive Windows account while Codex commands run
as a sandbox account. Configure this clone once so ordinary Git commands accept it:

```console
git config --global --add safe.directory C:/Projects/localswim
```

After that setting exists, Git accepts the clone. The sandbox account may still be
unable to read the interactive user's global excludes file, so use these warning-free
forms while working here:

```console
git -c core.excludesFile=/dev/null status --short
git -c core.excludesFile=/dev/null diff --check
git -c core.excludesFile=/dev/null diff -- <paths>
```

If the global setting is not available in a fresh sandbox, add both overrides:

```console
git -c safe.directory=C:/Projects/localswim -c core.excludesFile=/dev/null status --short
```

The excludes override disables only the inaccessible user-level ignore file; this
repository's `.gitignore` remains in effect. Do not change repository Git configuration
merely to suppress a sandbox-only warning.

## Branch and publishing policy

GitHub permits direct `main` pushes only from `TerryOtt` and the installed
`chatgpt-codex-connector` GitHub App. Local Codex sessions use Terry's Git credential
and therefore appear to GitHub as `TerryOtt`; GitHub cannot audit them as a separate
actor. Cloud Codex appears under the App identity. Every other independently
authenticated actor must work on a lower-case kebab-case `feature/<terse-description>`
branch and submit a pull request to `main`.

The direct-push exception is capability, not standing authorization. Codex must still
wait for an explicit user request before committing or pushing. When authorized to
publish directly, use plain non-interactive Git commands from `main`; when preparing
work for any other contributor, follow `CONTRIBUTING.md`.

The active server-side rule must match `.github/rulesets/main.json`, and
`.github/workflows/contribution-policy.yml` supplies its required branch-name check.
The repository merge settings must match `.github/repository-settings.json`: only
squash merges are permitted, both required checks gate a PR, per-PR auto-merge is
available, and merged same-repository branches are deleted. The one current approval
RFC 2119 MUST come from GitHub user `TerryOtt`; `.github/CODEOWNERS` and the ruleset's
required code-owner review enforce that requirement. Approval by anyone else does not
satisfy it.
Do not create duplicate rulesets: inspect GitHub first and update the existing rule by
ID. The GitHub CLI token needs both `repo` and `workflow` scopes to push changes under
`.github/workflows/`; refresh it with
`gh auth refresh -h github.com -s repo -s workflow` when necessary. Never print, copy,
or persist the `gh` authentication token.
