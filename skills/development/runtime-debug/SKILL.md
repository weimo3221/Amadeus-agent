---
name: runtime-debug
description: Debug Python runtime behavior with small reproductions, evidence gathering, and focused fixes.
preferred_tools:
  - search_files
  - read_file
  - patch
  - write_file
allowed_tools:
  - search_files
  - read_file
  - patch
  - write_file
---

# Runtime Debug

Work from evidence first.

1. Find the exact runtime entrypoint, tests, or failing module before proposing a fix.
2. Prefer the smallest reproduction that isolates the bug.
3. Keep edits local to the runtime, its tests, and directly affected docs.
4. After changing runtime behavior, update or add the narrowest regression test that proves the fix.
