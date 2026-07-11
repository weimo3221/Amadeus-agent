from __future__ import annotations

import importlib.util
import math
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


BGE_M3_PROVIDER_ID = "local_bge_m3"
BGE_M3_MODEL_ID = "BAAI/bge-m3"
BGE_M3_DIMENSIONS = 1024
EMBEDDING_DEPENDENCY_SPECS = (
    "huggingface_hub>=0.23",
    "FlagEmbedding>=1.2.10",
)
EMBEDDING_DEPENDENCY_MODULES = (
    "huggingface_hub",
    "FlagEmbedding",
)
DeploymentStatus = Literal["idle", "running", "completed", "cancelled", "failed"]


class EmbeddingDeploymentCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalEmbeddingConfig:
    provider: str
    model_id: str
    local_dir: Path
    dimensions: int = BGE_M3_DIMENSIONS
    normalize_embeddings: bool = True
    batch_size: int = 8
    device: str = "auto"


@dataclass
class EmbeddingDeploymentState:
    status: DeploymentStatus = "idle"
    phase: str = "idle"
    message: str = ""
    error: str = ""
    started_at: str = ""
    finished_at: str = ""
    model_id: str = BGE_M3_MODEL_ID
    local_dir: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "phase": self.phase,
            "message": self.message,
            "error": self.error,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "modelId": self.model_id,
            "localDir": self.local_dir,
            "active": self.status == "running",
        }


class LocalEmbeddingDeploymentManager:
    def __init__(self, *, repo_root: Path, default_model_dir: Path | None = None) -> None:
        self.repo_root = repo_root
        self.default_model_dir = default_model_dir or repo_root / "models" / "embeddings" / "bge-m3"
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._state = EmbeddingDeploymentState(local_dir=str(self.default_model_dir))

    def status(self, config: LocalEmbeddingConfig | None = None) -> dict[str, Any]:
        active_config = config or LocalEmbeddingConfig(
            provider=BGE_M3_PROVIDER_ID,
            model_id=BGE_M3_MODEL_ID,
            local_dir=self.default_model_dir,
        )
        with self._lock:
            deployment = self._state.to_payload()

        dependency_status = dependency_status_payload()
        model_installed = is_model_installed(active_config.local_dir)
        return {
            "configured": active_config.provider == BGE_M3_PROVIDER_ID,
            "provider": active_config.provider,
            "modelId": active_config.model_id,
            "localDir": str(active_config.local_dir),
            "dimensions": active_config.dimensions,
            "normalizeEmbeddings": active_config.normalize_embeddings,
            "batchSize": active_config.batch_size,
            "device": active_config.device,
            "dependenciesInstalled": dependency_status["installed"],
            "dependencyModules": dependency_status["modules"],
            "dependencyInstallCommand": dependency_status["installCommand"],
            "modelInstalled": model_installed,
            "deployed": dependency_status["installed"] and model_installed,
            "deployment": deployment,
        }

    def deploy(self, config: LocalEmbeddingConfig, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._state.status == "running":
                return_running = True
            else:
                return_running = False

        if return_running:
            return self.status(config)

        if self.status(config)["deployed"] and not force:
            with self._lock:
                now = now_iso()
                self._state = EmbeddingDeploymentState(
                    status="completed",
                    phase="ready",
                    message="BGE-M3 is already installed locally.",
                    started_at=now,
                    finished_at=now,
                    model_id=config.model_id,
                    local_dir=str(config.local_dir),
                )
            return self.status(config)

        with self._lock:
            self._cancel_event.clear()
            self._state = EmbeddingDeploymentState(
                status="running",
                phase="queued",
                message="Preparing local BGE-M3 deployment.",
                started_at=now_iso(),
                model_id=config.model_id,
                local_dir=str(config.local_dir),
            )
            self._thread = threading.Thread(
                target=self._deploy_worker,
                args=(config,),
                name="bge-m3-deploy",
                daemon=True,
            )
            self._thread.start()
        return self.status(config)

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            running = self._state.status == "running"
            process = self._process
            if running:
                self._cancel_event.set()
                self._state.phase = "cancelling"
                self._state.message = "Cancelling local BGE-M3 deployment."
            payload = self._state.to_payload()

        if process is not None and process.poll() is None:
            process.terminate()

        return {
            "cancelled": running,
            "deployment": payload,
        }

    def _deploy_worker(self, config: LocalEmbeddingConfig) -> None:
        try:
            self._set_running("dependencies", "Checking optional embedding dependencies.")
            if not dependencies_installed():
                self._run_command(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--upgrade",
                        *EMBEDDING_DEPENDENCY_SPECS,
                    ],
                    phase="installing_dependencies",
                    message="Installing huggingface_hub and FlagEmbedding.",
                )

            self._ensure_not_cancelled()
            self._set_running("downloading_model", f"Downloading {config.model_id} into {config.local_dir}.")
            config.local_dir.mkdir(parents=True, exist_ok=True)
            self._run_command(
                [
                    sys.executable,
                    "-c",
                    (
                        "from huggingface_hub import snapshot_download; "
                        "snapshot_download("
                        f"repo_id={config.model_id!r}, "
                        f"local_dir={str(config.local_dir)!r}, "
                        "local_dir_use_symlinks=False"
                        ")"
                    ),
                ],
                phase="downloading_model",
                message="Downloading BGE-M3 model files from Hugging Face.",
            )

            self._ensure_not_cancelled()
            self._set_running("verifying", "Verifying local BGE-M3 files.")
            if not is_model_installed(config.local_dir):
                raise RuntimeError("BGE-M3 download finished, but required model files were not found.")

            self._run_command(
                [
                    sys.executable,
                    "-c",
                    (
                        "from FlagEmbedding import BGEM3FlagModel; "
                        f"BGEM3FlagModel({str(config.local_dir)!r}, use_fp16=True)"
                    ),
                ],
                phase="loading_model",
                message="Loading BGE-M3 with FlagEmbedding to verify the local cache.",
            )

            self._finish("completed", "ready", "BGE-M3 is installed and ready for local embedding.")
        except EmbeddingDeploymentCancelled:
            self._finish("cancelled", "cancelled", "BGE-M3 deployment was cancelled.")
        except Exception as error:
            self._finish("failed", "failed", "BGE-M3 deployment failed.", error=str(error))
        finally:
            with self._lock:
                self._process = None

    def _run_command(self, command: list[str], *, phase: str, message: str) -> None:
        self._ensure_not_cancelled()
        self._set_running(phase, message)
        process = subprocess.Popen(
            command,
            cwd=str(self.repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        with self._lock:
            self._process = process

        while True:
            self._ensure_not_cancelled()
            return_code = process.poll()
            if return_code is not None:
                with self._lock:
                    if self._process is process:
                        self._process = None
                if return_code != 0:
                    raise RuntimeError(f"command failed during {phase} with exit code {return_code}")
                return
            self._cancel_event.wait(1.0)

    def _ensure_not_cancelled(self) -> None:
        if not self._cancel_event.is_set():
            return
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        raise EmbeddingDeploymentCancelled()

    def _set_running(self, phase: str, message: str) -> None:
        with self._lock:
            self._state.status = "running"
            self._state.phase = phase
            self._state.message = message

    def _finish(self, status: DeploymentStatus, phase: str, message: str, *, error: str = "") -> None:
        with self._lock:
            self._state.status = status
            self._state.phase = phase
            self._state.message = message
            self._state.error = error
            self._state.finished_at = now_iso()


def dependencies_installed() -> bool:
    return all(importlib.util.find_spec(module_name) is not None for module_name in EMBEDDING_DEPENDENCY_MODULES)


class LocalBGEM3EmbeddingProvider:
    name = BGE_M3_PROVIDER_ID

    def __init__(self, config: LocalEmbeddingConfig) -> None:
        self.config = config
        self.provider = config.provider
        self.model_id = config.model_id
        self.dimensions = config.dimensions
        self._lock = threading.Lock()
        self._model: Any | None = None

    def available(self) -> bool:
        return self.config.provider == BGE_M3_PROVIDER_ID and dependencies_installed() and is_model_installed(self.config.local_dir)

    def encode_texts(self, texts: list[str] | tuple[str, ...]) -> list[list[float]]:
        normalized_texts = [str(text or "").strip() for text in texts]
        if not normalized_texts:
            return []
        if not self.available():
            raise RuntimeError("BGE-M3 embedding provider is not deployed")
        model = self._load_model()
        encoded = model.encode(
            normalized_texts,
            batch_size=max(1, int(self.config.batch_size)),
            max_length=8192,
        )
        dense_vectors = encoded.get("dense_vecs") if isinstance(encoded, dict) else encoded
        vectors = normalize_dense_vectors(dense_vectors)
        if self.config.normalize_embeddings:
            vectors = [l2_normalize_vector(vector) for vector in vectors]
        if any(len(vector) != self.config.dimensions for vector in vectors):
            dimensions = sorted({len(vector) for vector in vectors})
            raise RuntimeError(f"BGE-M3 embedding dimension mismatch: expected {self.config.dimensions}, got {dimensions}")
        return vectors

    def _load_model(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from FlagEmbedding import BGEM3FlagModel  # type: ignore
            except Exception as error:
                raise RuntimeError("FlagEmbedding is not installed") from error
            self._model = BGEM3FlagModel(str(self.config.local_dir), use_fp16=True)
            return self._model


def normalize_dense_vectors(raw_vectors: Any) -> list[list[float]]:
    if hasattr(raw_vectors, "tolist"):
        raw_vectors = raw_vectors.tolist()
    if raw_vectors is None:
        return []
    if isinstance(raw_vectors, tuple):
        raw_vectors = list(raw_vectors)
    if not isinstance(raw_vectors, list):
        raise RuntimeError("embedding provider returned an unsupported vector payload")
    if raw_vectors and all(isinstance(value, (int, float)) for value in raw_vectors):
        raw_vectors = [raw_vectors]

    vectors: list[list[float]] = []
    for raw_vector in raw_vectors:
        if hasattr(raw_vector, "tolist"):
            raw_vector = raw_vector.tolist()
        if isinstance(raw_vector, tuple):
            raw_vector = list(raw_vector)
        if not isinstance(raw_vector, list):
            raise RuntimeError("embedding provider returned a non-list vector")
        vector: list[float] = []
        for value in raw_vector:
            try:
                parsed = float(value)
            except (TypeError, ValueError) as error:
                raise RuntimeError("embedding provider returned a non-numeric vector value") from error
            if not math.isfinite(parsed):
                raise RuntimeError("embedding provider returned a non-finite vector value")
            vector.append(parsed)
        vectors.append(vector)
    return vectors


def l2_normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return list(vector)
    return [value / norm for value in vector]


def dependency_status_payload() -> dict[str, Any]:
    modules = {
        module_name: importlib.util.find_spec(module_name) is not None
        for module_name in EMBEDDING_DEPENDENCY_MODULES
    }
    return {
        "installed": all(modules.values()),
        "modules": modules,
        "installCommand": f"{sys.executable} -m pip install --upgrade {' '.join(EMBEDDING_DEPENDENCY_SPECS)}",
    }


def is_model_installed(local_dir: Path) -> bool:
    if not local_dir.exists() or not local_dir.is_dir():
        return False
    has_config = (local_dir / "config.json").exists()
    has_weights = any((local_dir / filename).exists() for filename in ("model.safetensors", "pytorch_model.bin"))
    has_tokenizer = any(
        (local_dir / filename).exists()
        for filename in ("tokenizer.json", "tokenizer_config.json", "sentencepiece.bpe.model")
    )
    return has_config and has_weights and has_tokenizer


def default_bge_m3_model_dir(repo_root: Path) -> Path:
    configured = os.environ.get("AMADEUS_BGE_M3_MODEL_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    root = os.environ.get("AMADEUS_EMBEDDING_MODELS_ROOT", "").strip()
    if root:
        return Path(root).expanduser() / "bge-m3"
    return repo_root / "models" / "embeddings" / "bge-m3"


def normalize_embedding_local_dir(value: Any, *, repo_root: Path) -> Path:
    if value is None:
        return default_bge_m3_model_dir(repo_root)
    if not isinstance(value, str):
        raise ValueError("localDir must be a string")
    stripped = value.strip()
    if not stripped:
        return default_bge_m3_model_dir(repo_root)
    path = Path(stripped).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path


def remove_embedding_model_cache(local_dir: Path) -> None:
    if local_dir.exists():
        shutil.rmtree(local_dir)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
