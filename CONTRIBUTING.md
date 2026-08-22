# Contributing to localswim

Read [the contributor orientation](docs/ORIENTATION.md) and the applicable
[repository guidance](AGENTS.md) before changing the implementation.

## Branch and pull-request policy

Only this GitHub identity may push directly to `main`:

- Terry, authenticated as GitHub user [`TerryOtt`](https://github.com/TerryOtt).

A local Codex session using Terry's Git credential is covered by that exception:
GitHub records its push as `TerryOtt`, not as a distinct actor. Repository instructions
still require Codex to commit or push only when Terry explicitly asks.

OpenAI's `chatgpt-codex-connector` GitHub App is not currently installed or authorized
for this repository. GitHub will not accept an uninstalled App as a ruleset bypass
actor. A future cloud Codex task therefore follows the feature-branch and PR policy
unless Terry installs that App for this repository and deliberately adds its App ID
`1144995` to the bypass list and recipe.

Every other contributor must:

1. Create a branch named `feature/<terse-description>`.
2. Use lower-case kebab case after the slash, for example
   `feature/reject-stale-card-edit`.
3. Push that branch and submit a pull request targeting `main`.
4. Wait for the `contribution-policy` and `gate` checks to pass.
5. Obtain an approving review from GitHub user `TerryOtt`, then use squash merge or a
   previously enabled per-PR auto-merge.

The enforced branch-name grammar is:

```text
^feature/[a-z0-9]+(-[a-z0-9]+)*$
```

Keep the description short and focused even though the check does not impose an
arbitrary word limit. Terry is exempt from this naming rule when choosing to use a
pull request.

## Where enforcement lives

- `.github/workflows/contribution-policy.yml` checks the source branch and PR author.
- `.github/rulesets/main.json` is the checked-in recipe for the active GitHub ruleset.
- `.github/repository-settings.json` is the checked-in recipe for merge methods,
  auto-merge availability, and branch cleanup.
- GitHub's active repository ruleset is the actual protection boundary. Committing
  either JSON recipe does not apply it automatically.

The approval requirement is an RFC 2119 **MUST**: approval must come from GitHub user
`TerryOtt`. Another review may be useful but does not satisfy the merge requirement.
`.github/CODEOWNERS` makes Terry the sole owner of every path, and the ruleset requires
a code-owner review plus one approving review of the current changes. It also requires
the `contribution-policy` and `gate` status checks. Squash is the only permitted PR
merge method. GitHub automatically deletes a same-repository head branch after its PR
is merged; it cannot delete a contributor's branch in a separate fork.

Repository auto-merge is available but is not automatically selected for every PR.
Someone with write permission must enable it on an individual PR; GitHub then performs
the squash merge after the required review and checks pass. Otherwise, Terry performs
the squash merge explicitly after approval.

## Maintaining the GitHub ruleset

Authenticate `gh` as a repository administrator, then inspect before changing server
state:

```console
gh auth status -h github.com
gh auth refresh -h github.com -s repo -s workflow
gh api repos/TerryOtt/localswim/rulesets
gh api repos/TerryOtt/localswim/rules/branches/main
gh api repos/TerryOtt/localswim --jq \
  '{allow_auto_merge,allow_merge_commit,allow_rebase_merge,allow_squash_merge,delete_branch_on_merge}'
```

The `workflow` scope is required when a commit creates or changes a file beneath
`.github/workflows/`. A token with `repo` but not `workflow` can administer this public
repository yet GitHub will still reject that push.

For a repository with no existing ruleset, apply the checked-in recipe once:

```console
gh api --method POST repos/TerryOtt/localswim/rulesets --input .github/rulesets/main.json
```

Apply the repository-level merge settings idempotently with:

```console
gh api --method PATCH repos/TerryOtt/localswim --input .github/repository-settings.json
```

Do not run that POST when the named ruleset already exists; it would create a second
ruleset. Update the existing ruleset by ID with `PUT`, then retrieve it and compare its
conditions, rules, and bypass actors with the recipe. GitHub intentionally returns
bypass actors only to callers with write access to the ruleset.

The persisted actor IDs are public GitHub identities, not credentials:

| Actor | Type | ID |
|---|---|---:|
| `TerryOtt` | User | `17037862` |
| `chatgpt-codex-connector` | Integration, not currently active | `1144995` |

Never store a GitHub token, service credential, board data, or service descriptor in
this repository.
