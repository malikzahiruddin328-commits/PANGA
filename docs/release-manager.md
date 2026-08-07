# Release manager convention

Panga regularly has several Claude Code sessions working in parallel, each
in its own worktree/branch (per Zahir's standing preference for one session
per purpose, so concurrent work doesn't collide - see CLAUDE.md and each
worktree's own scope doc if it has one). That's good for isolation, but
integration back into `master` is where those sessions can step on each
other: stale processes on shared ports, a branch that's drifted behind
`master`'s own forward progress, merge conflicts nobody's looking at until
someone tries to ship.

**Release manager** is the convention for closing that gap: a session
(usually a dedicated one, opened when there's integration work to do -
not a standing background job) that owns getting a finished worktree
branch onto `master` safely. It does not write new features. If it notices
something feature-shaped needs doing, it flags it rather than doing it
inline - same rule Bhangi follows for its own scope
(`../Bhangi` README/docs).

## What it does, in order

1. **Confirm the branch is actually done** - the owning session says so, or
   its own scope doc / final report says so. Don't merge work still in
   progress.
2. **Check `master`'s current tip**, not just the branch's original merge
   base - `master` moves while a feature branch is being worked, and a
   feature branch review from an earlier point can miss what's landed
   since. `git log --oneline master -5` before doing anything else.
3. **Preview the merge before touching anything**: `git merge-base <base>
   master <branch>` then `git merge-tree <base> master <branch>` to see
   conflicts without touching any working tree - decide whether they're
   real (two people changed the same logic) or just textual (both added
   independent code near the same line, e.g. two new tab entries).
4. **Resolve inside the feature branch's own worktree**, never in the
   shared `master` checkout other sessions may have dirty files in -
   `git merge master --no-edit` there, fix conflicts, commit. This keeps
   `master`'s working tree untouched by conflict resolution in progress.
5. **Run the full regression suite** after resolving - not just the new
   feature's tests, the whole `pytest` run - before it goes anywhere near
   `master`.
6. **Fast-forward `master`** (`git merge <branch> --ff-only` from the real
   `master` checkout) once the merged branch is verified. `--ff-only` is
   the guardrail: if it's not a clean fast-forward, something about step 2
   was missed and it needs to go back through merge-in-the-worktree first,
   not a forced/manual merge on `master` itself.
7. **Never touch unrelated uncommitted changes** sitting in the `master`
   checkout from another active session (e.g. a stray `run_app.bat` edit)
   - a fast-forward that doesn't touch those files won't disturb them;
   confirm with `git status --short` before and after that the only diff
   is the merge itself.
8. **Verify live**, not just via tests, when the change is UI-observable -
   launch the app from its real location (not a worktree path, so sibling
   imports like Bhangi's resolve the way they will in production) and
   click through the actual change.
9. **Report back**: commit hashes on both branches, test results, what was
   verified live, and confirmation the target checkout's pre-existing
   uncommitted state was left alone.
10. **Clean up** the now-merged worktree and branch
    (`git worktree remove`, `git branch -d`) once `master` has the work -
    a merged worktree just sitting there is one more thing to keep track
    of for no benefit. **Re-check the branch's tip immediately before
    removing anything** (`git log --oneline <branch> -1`) - the owning
    session can land a new commit (e.g. its own proactive self-review)
    in the gap between your merge and your cleanup, and a `git worktree
    remove --force` doesn't warn you about that the way `git branch -d`
    does. If `git branch -d` refuses ("not fully merged"), treat that as
    a hard stop-and-recheck signal, not something to force past - re-run
    the merge for whatever new commit(s) showed up, then clean up again.
    Discovered live 2026-08-06: a worktree was force-removed mid-cleanup
    right as a 9th commit landed on it: no data was lost (removing a
    worktree doesn't delete commits), but only `git branch -d`'s refusal
    caught the gap - don't rely on that safety net being the only check.

## What it explicitly does not do

- Write feature code, refactor, or "improve while I'm in there" - that's
  the owning session's job, not the release manager's.
- Force-push, rebase `master`, or merge with a failing test suite.
- Merge two feature branches into each other directly - always through
  `master`, so `master` stays the single source of truth every branch
  measures itself against.
- Resolve a conflict by guessing intent when it's a real logic collision
  (not just two independent additions near the same line) - flag it back
  to Zahir instead of picking a side unasked.
