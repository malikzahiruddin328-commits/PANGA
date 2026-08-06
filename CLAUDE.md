# Panga — coding principles

- **Check for race conditions**: any code touching shared state that multiple
  processes/tasks/threads can hit concurrently (e.g. the 3 scheduled tasks
  and the Streamlit app all touching the same JSON stores or Gmail labels)
  must be reviewed for races before shipping. Prefer read-modify-write
  patterns that are safe if run twice, or explicit file locking, over
  assuming single-writer.
- **Check for infinite loops**: any loop bound by an external condition
  (waiting on an API, a file, a Gmail label change) needs a max-iteration or
  timeout bound, not an unconditional `while True`.
- **Check for locking errors**: watch for deadlocks/starvation when adding
  any lock, and for unhandled exceptions that could leave a lock held.
- **Design with performance in mind**: avoid unnecessary full-file
  rewrites/reloads on hot paths, avoid O(n^2) scans over growing stores
  (jobs.json is already 300+ records and growing daily), consider the cost
  of an operation before adding it to something that runs 4x/day or every
  10 minutes.

Apply these checks whenever writing or reviewing code in this repo, not just
when explicitly asked.

- **Apply HCI principles to every UI change, proactively, not reactively.**
  2026-07-31: a string of small UI fixes this session (checkbox row-select
  → click-to-activate, no auto-scroll to a freshly opened detail panel,
  stale prefilled text in widgets after a regenerate, three related actions
  needing three separate buttons, a full-width field wasting vertical
  space) were each individually reported by Zahir one at a time. He pointed
  out that catching these up front as a standing practice would have made
  most of that back-and-forth unnecessary. Before shipping a UI change,
  check it against basics like:
  - Does an action need scrolling to see its result? If Streamlit re-renders
    the page top-to-bottom and the outcome lands off-screen, consider an
    anchor + `st.html(..., unsafe_allow_javascript=True)` scroll-into-view
    (not iframed, so this works - see the Results-tab Role-click handler).
  - Does a widget keep showing stale content after the data behind it
    changes? Streamlit ignores a new `value=` once a `key` already has
    session-state - fold whatever changed into the key (see the
    strategy-tag and clarifying-question boxes for the pattern), or the
    box silently lies about current state.
  - Are related decisions forced into separate clicks/buttons when they're
    really one decision made at one moment? Consolidate.
  - Does a suggested/prefilled value risk being mistaken for a fact or for
    the user's own answer? Keep genuine guesses hedged and editable (never
    asserted); keep things that need the user's real reasoning (not a
    guess at all) as an empty box with a placeholder, not a prefill.
  - Is space used proportionally to importance - is a field taking a full
    row when it could sit beside a related one at the same height?
  This isn't a fixed checklist to run mechanically - it's the standard to
  hold every UI change to, the same way the checks above apply to every
  code change.

## Be proactive, not reactive — on everything, not just UI

Every session working in this repo (not just the hub) should anticipate
foreseeable problems and fix/flag them before they cause a real bug or a
Zahir report, not wait to be told. This extends the existing HCI and
race-condition/performance checks above into a general standing posture:

- If you're about to establish a new pattern that other sessions will also
  do in parallel (a new scheduled process, a new shared file, a new way of
  spinning up a dev server), ask what breaks at scale once several sessions
  are doing it independently, and fix that up front - don't wait for
  someone to hit the collision.
- If you notice something adjacent to your task that's wrong, stale, or
  risky (a comment claiming behavior the code doesn't have, a leaked
  process, an untested assumption), flag it or fix it (if in scope) rather
  than only doing the narrow thing you were asked for. This is the same
  instinct behind the JD-fetch gap, the port-sprawl cleanup, and the
  Mirror doc-vs-code audit - each was worth catching before Zahir had to
  name the exact problem.
- Verify against real data/state before reporting something as fact - a
  claim (yours or another session's) that looks right in code or in a
  self-reported summary can still be wrong in practice. Check the actual
  running state, the actual stored data, the actual file on disk.
- When you finish a task, do a quick "what would break next" pass before
  reporting done - not just "does this satisfy the literal ask."

2026-08-06: Zahir made this explicit after having to personally spot and
name a port-isolation issue (prod vs. test dev-server ports colliding)
that was a predictable consequence of the multi-session setup itself -
his point wasn't that the fix was wrong, it's that this class of thing
should be caught proactively, not reactively explained to the AI by him.

**Port convention (concrete example of the above):** production is always
port 8510, launched only via `run_app.bat` (which kills-and-restarts on
that port - never anything else touches 8510). Dev/test live-verification
uses one of the named slots in `.claude/launch.json` (8501-8509 range) -
never invent an ad hoc port. If every slot is in use, stop one (see
"stop preview servers" below) rather than freelancing a new number -
that's exactly the anti-pattern that caused a real leak (2026-08-06, 4
zombie instances on ports nobody had reserved). `panga-ui-rm-verify`
(8509) is reserved exclusively for Release Manager's merge-verification -
no other session should use it.

**Testing a worktree's own unmerged code, not the main checkout:** the
named slots in `.claude/launch.json` (via the Browser tool's `preview_start`)
always run from the main checkout's directory, regardless of which worktree
you call them from - fine for Release Manager's post-merge checks, useless
for live-verifying a branch that hasn't merged yet. There's also no
per-worktree `venv/` - it only exists at the root checkout. To live-test a
worktree's own code, run streamlit manually from inside that worktree using
the root venv's absolute path, e.g. (adjust the branch name and port - pick
an unused slot from the 8501-8509 range, same rule as always):

```
cd .claude/worktrees/<branch-name>
"C:\Users\User\Desktop\Myra\Panga\venv\Scripts\streamlit.exe" run src/ui/app.py --server.headless true --server.port 8506
```

(2026-08-06: UI refinement hit this while live-verifying the CTA-stat-strip
branch and had to figure it out ad hoc - documenting so the next session
doesn't have to.)

**Never kill a process on a shared port without confirming you own it
first.** Check its command line / start time / working directory before
killing - don't infer ownership just because it's inconvenient or recent.
If you can't confirm it's yours, ask the hub rather than kill-and-see.
(2026-08-06: Release Manager killed two PIDs on a shared manual-verify
slot based on inference, not confirmation - no harm done this time, but
it could just as easily have taken out another session's live check. This
is the same "route judgment calls through the hub" principle applied to
shared infrastructure, not just decisions.)

## Processing LinkedIn job-alert emails into job records

There is no automated script for this yet - it's currently done by a
Claude Code session manually reading Zahir's LinkedIn email digests and
adding postings via `add_manual_job()`. **Add every listing found, never
skip one because it looks like the wrong industry/vertical** (Zahir's
explicit instruction, 2026-08-06) - industry/vertical relevance is the
scoring pipeline's job (`fit_score`, shown to Zahir so he can judge for
himself), not a reason to silently never add a record in the first place.
A dropped-at-intake job never even reaches him to evaluate; a low-scored
one still does.

## Merging a finished worktree branch into master

Panga usually has several sessions working in parallel worktrees. When a
branch is ready to integrate, follow the **release manager** convention in
`docs/release-manager.md` rather than improvising: check master's current
tip (it moves while you work), resolve conflicts inside the feature
branch's own worktree, run the full suite, then fast-forward master -
never merge conflict-resolution work directly in the shared master
checkout, and never touch another session's unrelated uncommitted files.
