# claude-status
Claude-Terry swimlane task tracker

A live, draggable board over one board JSON, served on loopback.

```
python serve.py <board.json>
```

The port comes from the board file's own `port` field rather than a flag, so one
bookmark per project cannot open the wrong board. `status.py` is the library and the
command line; `rules.json` is the permission model and is re-read live.

## The gate

```
python check.py
```

Runs `ruff` and the US English / house-vocabulary check over every tracked `.py`, `.md`
and `.json`. **Run it before committing.**

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
