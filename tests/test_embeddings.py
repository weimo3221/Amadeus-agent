from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.embeddings import (
    BGE_M3_MODEL_ID,
    BGE_M3_PROVIDER_ID,
    LocalEmbeddingConfig,
    LocalEmbeddingDeploymentManager,
    is_model_installed,
    normalize_embedding_local_dir,
)


class EmbeddingDeploymentTests(unittest.TestCase):
    def test_model_installed_requires_config_weights_and_tokenizer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model_dir = Path(tmpdir) / "bge-m3"
            model_dir.mkdir()

            self.assertFalse(is_model_installed(model_dir))

            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")

            self.assertTrue(is_model_installed(model_dir))

    def test_status_reports_deployed_when_dependencies_and_files_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            model_dir = repo_root / "models" / "embeddings" / "bge-m3"
            model_dir.mkdir(parents=True)
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            (model_dir / "model.safetensors").write_bytes(b"weights")
            (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
            manager = LocalEmbeddingDeploymentManager(repo_root=repo_root, default_model_dir=model_dir)
            config = LocalEmbeddingConfig(
                provider=BGE_M3_PROVIDER_ID,
                model_id=BGE_M3_MODEL_ID,
                local_dir=model_dir,
            )

            with patch("amadeus.embeddings.dependency_status_payload", return_value={
                "installed": True,
                "modules": {"huggingface_hub": True, "FlagEmbedding": True},
                "installCommand": "python -m pip install",
            }):
                status = manager.status(config)

            self.assertTrue(status["deployed"])
            self.assertTrue(status["modelInstalled"])
            self.assertTrue(status["dependenciesInstalled"])

    def test_normalize_embedding_local_dir_resolves_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            self.assertEqual(
                normalize_embedding_local_dir("models/embeddings/bge-m3", repo_root=repo_root),
                repo_root / "models" / "embeddings" / "bge-m3",
            )


if __name__ == "__main__":
    unittest.main()
