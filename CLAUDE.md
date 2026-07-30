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
