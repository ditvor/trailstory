---
description: Fetch origin, fast-forward local `develop`, and delete branches that have already been merged
---

# /sync-develop — keep local `develop` and branch list clean

Catch local state up to `origin/develop` after a PR is merged: fast-forward the
local `develop`, then prune feature branches whose work has already landed.
**Pause and confirm before deleting branches** — never destroy work without an
explicit OK.

## Steps

1. **Fetch and prune.**
   - `git fetch --all --prune` — picks up new commits and removes
     remote-tracking refs that no longer exist upstream.

2. **Fast-forward local `develop`.**
   - Note the current branch.
   - `git checkout develop`. If the working tree has uncommitted changes that
     would block the checkout, stop and tell the user — do **not** stash or
     `checkout --force`.
   - `git merge --ff-only origin/develop`.
     - If this fails because local `develop` has commits not on
       `origin/develop`, stop. Surface the divergence and ask the user how to
       proceed (most likely they need to rebase or open a PR — never reset
       hard or force-push to fix this).
   - Print the new HEAD of `develop` so the user can see what came in.

3. **Identify merge candidates.**
   - List local branches whose tip is reachable from `origin/develop`:
     ```bash
     git branch --merged origin/develop --format='%(refname:short)' \
       | grep -vE '^(develop|main)$'
     ```
   - These are safe deletion candidates.
   - Also list branches whose remote has been deleted (PR was squash-merged,
     remote-tracking branch is gone) but whose local copy still exists. Those
     show up with `git branch -vv | grep ': gone]'`.

4. **Confirm before deleting.**
   - Show the user the full list of candidates.
   - Ask explicitly: "Delete these N branches? (y/N)".
   - On 'y', delete one at a time. Prefer `git branch -d <name>` — it refuses
     to delete unmerged work. If `-d` rejects a branch that the user is
     certain is safe (e.g. squash-merged so commits look "unmerged"), call out
     the rejection and ask before falling back to `git branch -D <name>`.

5. **Return to the original branch.**
   - If the user was on a branch other than `develop` and that branch still
     exists, `git checkout <original-branch>`. Otherwise stay on `develop`.

6. **Summarise.**
   - Report: `develop` HEAD, branches deleted, branches skipped, branches that
     refused `-d`.

## Notes

- Never `git reset --hard` or `git push --force` to "fix" divergence.
- Never delete `develop` or `main`.
- If `git fetch` shows new commits that look surprising (rewritten history,
  force-push), stop and surface them to the user.
