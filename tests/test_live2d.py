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

    def test_list_models_marks_active_and_reads_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            free_dir = root / "models" / "hiyori-free"
            pro_dir = root / "models" / "hiyori-pro"
            free_dir.mkdir(parents=True)
            pro_dir.mkdir(parents=True)
            (free_dir / "hiyori.model3.json").write_text("{}", encoding="utf-8")
            (pro_dir / "hiyori_pro.model3.json").write_text("{}", encoding="utf-8")
            (free_dir / "manifest.yaml").write_text(
                "\n".join([
                    "displayName: Hiyori Free",
                    "defaults:",
                    "  expression: neutral",
                    "  motion: idle",
                    "aliases:",
                    "  motions:",
                    "    talk: [TapBody, Idle]",
                ]),
                encoding="utf-8",
            )
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
            library = LocalLive2DModelLibrary(root / "models", "http://127.0.0.1:8790", config_path)

            models = library.list_models()

        self.assertEqual(len(models), 2)
        free_model = next(model for model in models if model["id"] == "hiyori-free")
        self.assertTrue(free_model["active"])
        self.assertEqual(free_model["manifest"]["displayName"], "Hiyori Free")
        self.assertEqual(free_model["manifest"]["defaults"]["motion"], "idle")
        self.assertEqual(free_model["manifest"]["aliases"]["motions"]["talk"], ["TapBody", "Idle"])

    def test_select_model_persists_harness_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            free_dir = root / "models" / "hiyori-free"
            pro_dir = root / "models" / "hiyori-pro"
            free_dir.mkdir(parents=True)
            pro_dir.mkdir(parents=True)
            (free_dir / "hiyori.model3.json").write_text("{}", encoding="utf-8")
            (pro_dir / "hiyori_pro.model3.json").write_text("{}", encoding="utf-8")
            config_path = root / "harnesses.yaml"
            config_path.write_text(
                "\n".join([
                    "harnesses:",
                    "  live2d:",
                    "    enabled: true",
                    "    adapter: desktop-live2d",
                    "    model:",
                    "      id: hiyori-free",
                    "      path: hiyori-free/hiyori.model3.json",
                ]),
                encoding="utf-8",
            )
            library = LocalLive2DModelLibrary(root / "models", "http://127.0.0.1:8790", config_path)

            selection = library.select_model("hiyori-pro")
            persisted = config_path.read_text(encoding="utf-8")

        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.model_id, "hiyori-pro")
        self.assertEqual(selection.relative_path, "hiyori-pro/hiyori_pro.model3.json")
        self.assertIn("id: hiyori-pro", persisted)
        self.assertIn("path: hiyori-pro/hiyori_pro.model3.json", persisted)

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
