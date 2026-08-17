"""Generic, project-agnostic concurrency helpers.

Deliberately kept free of any Panga-specific concept (no "job",
"application", "basket", etc.) so this package can be copied or imported
as-is into other projects (e.g. C2) that need the same "run up to N things
concurrently, but back off under real local load" behavior."""
