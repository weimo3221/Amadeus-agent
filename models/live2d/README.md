# Live2D Models

This directory stores local Live2D Cubism models that can be loaded by the desktop renderer through the Amadeus HTTP server.

Directory convention:

```text
models/live2d/
  hiyori-free/
    hiyori_free_t08.model3.json
    hiyori_free_t08.moc3
    hiyori_free_t08.2048/
    motion/
  hiyori-pro/
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
