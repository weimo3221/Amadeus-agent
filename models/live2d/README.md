# Live2D Models

This directory stores local Live2D Cubism models that can be loaded by the desktop renderer through the Amadeus HTTP server.

Directory convention:

```text
models/live2d/
  hiyori-free/
    manifest.yaml
    hiyori_free_t08.model3.json
    hiyori_free_t08.moc3
    hiyori_free_t08.2048/
    motion/
  hiyori-pro/
    manifest.yaml
    hiyori_pro_t11.model3.json
    hiyori_pro_t11.moc3
    hiyori_pro_t11.2048/
    motion/
```

Switch the active model in `configs/harnesses.yaml`:

```yaml
harnesses:
  live2d:
    model:
      id: hiyori-free
      path: hiyori-free/hiyori_free_t08.model3.json
```

If `path` is empty, the runtime will look for the first `*.model3.json` under `models/live2d/<id>/`.

Optional `manifest.yaml` files can describe display names, default idle behavior, and per-model aliases for semantic expressions/motions:

```yaml
displayName: My Model
defaults:
  expression: neutral
  motion: idle
aliases:
  expressions:
    smile: [smile, happy]
    serious: [serious, focused]
  motions:
    idle: [Idle, idle]
    talk: [TapBody, Idle]
```

The desktop renderer still discovers the actual supported expression and motion groups from the loaded `*.model3.json`; the manifest only guides semantic fallback names such as `talk`, `think`, or `smile`.
