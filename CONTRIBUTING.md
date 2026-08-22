# Contributing to localswim

Read [the contributor orientation](docs/ORIENTATION.md) and the applicable
[repository guidance](AGENTS.md) before changing the implementation.

## Branch and pull-request policy

Only these GitHub actors may push directly to `main`:

- Terry, authenticated as GitHub user [`TerryOtt`](https://github.com/TerryOtt).
- Codex cloud, authenticated as the
  [`chatgpt-codex-connector`](https://github.com/apps/chatgpt-codex-connector)
  GitHub App.

A local Codex session using Terry's Git credential is not a distinct GitHub actor;
GitHub records that push as `TerryOtt`. Repository instructions still require Codex
to commit or push only when Terry explicitly asks.

Every other contributor must:

1. Create a branch named `feature/<terse-description>`.
2. Use lower-case kebab case after the slash, for example
   `feature/reject-stale-card-edit`.
3. Push that branch and submit a pull request targeting `main`.
4. Wait for the `contribution-policy` and `gate` checks to pass before asking Terry
   to merge it.

The enforced branch-name grammar is:

```text
^feature/[a-z0-9]+(-[a-z0-9]+)*$
```

Keep the description short and focused even though the check does not impose an
arbitrary word limit. Terry and the Codex GitHub App are exempt from this naming rule
when they choose to use a pull request.

## Where enforcement lives

- `.github/workflows/contribution-policy.yml` checks the source branch and PR author.
- `.github/rulesets/main.json` is the checked-in recipe for the active GitHub ruleset.
- GitHub's active repository ruleset is the actual protection boundary. Committing
  the JSON recipe does not apply it automatically.

The ruleset requires a pull request and the `contribution-policy` status check, but it
does not prescribe a fixed number of approving reviews. The existing `gate` workflow
runs on every push and pull request; contributors should treat a green gate as a merge
prerequisite even though the narrowly scoped ruleset does not make that check a second
branch-protection requirement.

## Maintaining the GitHub ruleset

Authenticate `gh` as a repository administrator, then inspect before changing server
state:

```console
gh auth status -h github.com
gh auth refresh -h github.com -s repo -s workflow
gh api repos/TerryOtt/localswim/rulesets
gh api repos/TerryOtt/localswim/rules/branches/main
```

The `workflow` scope is required when a commit creates or changes a file beneath
`.github/workflows/`. A token with `repo` but not `workflow` can administer this public
repository yet GitHub will still reject that push.

For a repository with no existing ruleset, apply the checked-in recipe once:

```console
gh api --method POST repos/TerryOtt/localswim/rulesets --input .github/rulesets/main.json
```

Do not run that POST when the named ruleset already exists; it would create a second
ruleset. Update the existing ruleset by ID instead, then retrieve it and compare its
conditions, rules, and bypass actors with the recipe. GitHub intentionally returns
bypass actors only to callers with write access to the ruleset.

The persisted actor IDs are public GitHub identities, not credentials:

| Actor | Type | ID |
|---|---|---:|
| `TerryOtt` | User | `17037862` |
| `chatgpt-codex-connector` | Integration | `1144995` |

Never store a GitHub token, service credential, board data, or service descriptor in
this repository.
