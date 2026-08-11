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
   not a forced/manual merge on `master` itself. **If the push itself gets
   rejected** (`! [rejected] master -> master (fetch first)` or similar) -
   this convention has always assumed the release manager is the only
   session that ever pushes to `origin/master` directly, and that
   assumption has never actually been tested against a real rejection.
   Don't force-push past it: `git fetch origin`, confirm what landed there
   that your local `master` doesn't have, and re-run from step 2 against
   the real new tip - the same "state moved while I wasn't looking"
   handling as 6a below, just one layer further out (the remote, not just
   a branch). Added 2026-08-10 (Release Manager's own conduct audit) as a
   documented fallback rather than leaving it to be improvised the first
   time it actually happens.
6a. **Re-check the branch's own tip (`git log --oneline <branch> -1`)
   immediately before running the fast-forward, not just at the start of
   review** - the owning session can land a new commit on the same branch
   in the gap between when review started and when the merge command
   actually runs. Discovered live 2026-08-09: a second real commit landed
   on `feature/reextract-keywords` mid-review and merged to master without
   ever being seen beforehand - caught only because the fast-forward's
   file list didn't match what had been diffed, and closed with a full
   retroactive review that happened to hold up clean. Don't rely on
   noticing a mismatch after the fact being the only safety net - this is
   the same "re-check tip immediately before acting" principle already in
   step 10's cleanup rule, just applied one step earlier, before the merge
   itself rather than only before removing the worktree.
7. **Never touch unrelated uncommitted changes** sitting in the `master`
   checkout from another active session (e.g. a stray `run_app.bat` edit)
   - a fast-forward that doesn't touch those files won't disturb them;
   confirm with `git status --short` before and after that the only diff
   is the merge itself.
8. **Verify live**, not just via tests, when the change is UI-observable -
   launch the app from its real location (not a worktree path, so sibling
   imports like Bhangi's resolve the way they will in production) and
   click through the actual change.
8a. **For any change touching a core, user-facing page** (Results tab
   above all - it's the app's central value driver), check the diff
   against `docs/mirror-audit-checklist.md`'s categories directly, or ask
   Mirror to, BEFORE reporting it merged/ready. Added 2026-08-09 after a
   same-day-built-and-merged feature (score-first resume flow) went
   straight to Zahir's live testing as its first real QA pass, producing a
   string of live-found bugs (a control's label lying about what it does,
   a score panel not doing what it claimed) that a checklist pass would
   have caught first. Tests passing was never the actual gap - nobody
   looked at it with fresh, adversarial eyes before handing it to Zahir.
   Green tests are necessary, not sufficient, for a core page.
8b. **After any merge, grep the touched files for a duplicate top-level
   `def`/`class` name** (a real, previously-unchecked collision class -
   two independently-fine branches can each add a function with the same
   name at different points in the same file; git's line-based diff
   merges that cleanly with no conflict, since neither branch's own lines
   overlap, but the second definition silently shadows the first in
   Python's own namespace - the shadowed one still exists in the file,
   still passes a syntax check, and just quietly never runs again).
   Checked retroactively across every file touched in a single night's
   worth of merges (2026-08-10, Release Manager's own conduct audit) and
   found none - but the check itself didn't exist as a standing step
   before that audit, it was improvised once in response to being asked
   to self-audit. A one-line pattern for a touched file: `grep -n "^def
   \|^class " <file> | sed -E 's/.*(def|class) ([A-Za-z_0-9]+).*/\2/' |
   sort | uniq -d` - anything it prints is a real problem, not a style
   nit.
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
    **Before ever using `git worktree remove --force`**, run `git status`
    inside that worktree first and read what it actually shows - `--force`
    exists to bypass the "untracked files present" block (e.g. a stray
    `.env` copied in for a live-credentials test), and it's tempting to
    reach for it reflexively once you know THAT'S the reason a plain
    remove refused - but it also silently bypasses the "real uncommitted
    tracked changes" block at the same time, with no way to tell which
    one you're actually overriding unless you looked first. Found in
    2026-08-10's conduct audit: `--force` was used once tonight on exactly
    this reasoning (a known leftover `.env`) without confirming first that
    the .env was the *only* thing being discarded - no harm resulted, but
    the habit gap is real and worth closing before it does.

## Doc-only commits (e.g. relayed from Backlog)

A distinct, narrower path from the numbered flow above, not a shortcut
through it: Backlog (or another session) relays a pure documentation edit
- typically a new/updated PRD backlog row - with no code changes. This
still goes through a worktree and branch exactly like any code change -
**there is no shared-checkout shortcut for this, regardless of how small
or doc-vs-code the change is.** (Corrected 2026-08-11: an earlier version
of this section documented committing doc-only relays directly to
`master`'s shared checkout, written down as an explicit exception during
Release Manager's own 2026-08-10 conduct audit. That directly conflicted
with a standing rule Zahir set the same night, "confirmed the hard way"
after two separate sessions - UI refinement and Prospector design - each
committed a doc-only edit straight to the shared checkout reasoning it
was lighter-weight than a real code change: "the rule is about WHERE a
commit lands [the shared checkout, at risk from another session's
concurrent `git stash`/checkout/reset], not about how small or
code-vs-doc the change is - there is no size or content-type carve-out."
Release Manager is not exempt from that rule either - flagged to the hub
2026-08-11 rather than assumed, confirmed explicitly: fix this section to
match, don't ask again.)

What's still required, every time: confirm the actual diff is doc-only
(`git diff --stat` shows only the expected file(s), nothing else) and that
its content genuinely matches what was relayed, before staging. **Re-run
that same check immediately before `git add`/`git commit`, not only once
at the start** - the same "state can change in the gap between check and
act" principle as 6a above, just applied to a shared doc file instead of a
branch tip. Discovered live 2026-08-10: a Backlog batch was diffed and
confirmed at 25 inserted rows, then staged and committed without
re-checking - the committed result actually had 29, because 4 more rows
landed in the same shared file in the gap between the check and the
`git add`. The extra content turned out to be legitimate, but it was
committed unreviewed, which is the real gap, not the content itself. If
the relayed edit is already sitting as an uncommitted diff in the shared
checkout (e.g. another session edited it there directly, itself a
violation of the no-exception rule above but not this session's mistake
to compound) - save it as a patch, revert the shared checkout, and apply
the patch inside a fresh worktree instead of building on top of where it
already landed.

What's explicitly skipped for this path, and why: the full `pytest` run
(step 5 above) - a change confirmed doc-only by the check above cannot
affect Python behavior, so running the suite anyway would just be
overhead with no real signal. This is the one documented exception to "run
the full suite before it goes anywhere near `master`" - it is NOT an
exception to going through a worktree, which every change (code or doc)
always does.

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
