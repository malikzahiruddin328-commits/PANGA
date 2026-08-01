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

## Merging a finished worktree branch into master

Panga usually has several sessions working in parallel worktrees. When a
branch is ready to integrate, follow the **release manager** convention in
`docs/release-manager.md` rather than improvising: check master's current
tip (it moves while you work), resolve conflicts inside the feature
branch's own worktree, run the full suite, then fast-forward master -
never merge conflict-resolution work directly in the shared master
checkout, and never touch another session's unrelated uncommitted files.
