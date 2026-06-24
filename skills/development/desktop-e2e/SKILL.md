---
name: desktop-e2e
description: Extend packaged Electron desktop E2E coverage with deterministic runtime stubs and user-visible assertions.
preferred_tools:
  - search_files
  - read_file
  - patch
allowed_tools:
  - search_files
  - read_file
  - patch
---

# Desktop E2E

When you work on desktop end-to-end coverage:

1. Use packaged Electron paths, not renderer-only mocks, unless the behavior is purely unit scoped.
2. Prefer deterministic stub runtime events over flaky timing assumptions.
3. Assert the user-visible contract first: chat log, permission UI, model switcher, playback feedback, or bridge events.
4. Keep E2E fixtures minimal and update nearby docs only when the tested product boundary changed.
