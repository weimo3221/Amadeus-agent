from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.live2d import LocalLive2DModelLibrary


class Live2DModelLibraryTests(unittest.TestCase):
    def test_configured_model_reads_harness_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            model_dir = root / "models" / "hiyori-free"
            model_dir.mkdir(parents=True)
            (model_dir / "hiyori.model3.json").write_text("{}", encoding="utf-8")
            config_path = root / "harnesses.yaml"
            config_path.write_text(
                "\n".join([
                    "harnesses:",
                    "  live2d:",
                    "    enabled: true",
                    "    model:",
                    "      id: hiyori-free",
                    "      path: hiyori-free/hiyori.model3.json",
                ]),
                encoding="utf-8",
            )
            library = LocalLive2DModelLibrary(root / "models", "http://127.0.0.1:8790")

            selection = library.configured_model(config_path)

        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.model_id, "hiyori-free")
        self.assertEqual(selection.relative_path, "hiyori-free/hiyori.model3.json")
        self.assertEqual(
            library.model_url(selection),
            "http://127.0.0.1:8790/live2d/models/hiyori-free/hiyori.model3.json",
        )

    def test_configured_model_can_find_model_by_id_when_path_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            model_dir = root / "models" / "hiyori-pro"
            model_dir.mkdir(parents=True)
            (model_dir / "hiyori_pro.model3.json").write_text("{}", encoding="utf-8")
            config_path = root / "harnesses.yaml"
            config_path.write_text(
                "\n".join([
                    "harnesses:",
                    "  live2d:",
                    "    enabled: true",
                    "    model:",
                    "      id: hiyori-pro",
                    "      path: \"\"",
                ]),
                encoding="utf-8",
            )
            library = LocalLive2DModelLibrary(root / "models", "http://127.0.0.1:8790")

            selection = library.configured_model(config_path)

        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.relative_path, "hiyori-pro/hiyori_pro.model3.json")

    def test_resolve_public_path_blocks_traversal_and_unsupported_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            model_dir = root / "models" / "hiyori"
            model_dir.mkdir(parents=True)
            model_file = model_dir / "hiyori.model3.json"
            model_file.write_text("{}", encoding="utf-8")
            (model_dir / "secret.txt").write_text("nope", encoding="utf-8")
            library = LocalLive2DModelLibrary(root / "models", "http://127.0.0.1:8790")

            self.assertEqual(library.resolve_public_path("hiyori/hiyori.model3.json"), model_file.resolve())
            self.assertIsNone(library.resolve_public_path("../outside.model3.json"))
            self.assertIsNone(library.resolve_public_path("hiyori/secret.txt"))


if __name__ == "__main__":
    unittest.main()
