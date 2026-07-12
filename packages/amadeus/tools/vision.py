from __future__ import annotations

import base64
import json
import os
import struct
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from amadeus.tools.base import ToolSpec
from amadeus.tools.search_files import is_inside, workspace_root_from_context


VISION_ENDPOINT_ENV = "AMADEUS_VISION_ENDPOINT"
VISION_API_KEY_ENV = "AMADEUS_VISION_API_KEY"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_VISION_TIMEOUT_SECONDS = 30


def _read_local_image(path_text: str, context: Any = None) -> tuple[Path | None, bytes | None, str | None]:
    workspace_root = workspace_root_from_context(context)
    path = (workspace_root / path_text).resolve()
    if not is_inside(path, workspace_root):
        return None, None, "path must be inside the project workspace"
    if not path.exists() or not path.is_file():
        return None, None, "path must point to an existing file"
    try:
        size = path.stat().st_size
    except OSError:
        return None, None, "could not inspect image file"
    if size > MAX_IMAGE_BYTES:
        return None, None, "image is too large"
    try:
        return path, path.read_bytes(), None
    except OSError:
        return None, None, "could not read image file"


def _image_metadata(data: bytes) -> dict[str, Any]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return {"format": "png", "width": width, "height": height}
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return {"format": "gif", "width": width, "height": height}
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(data):
                break
            segment_length = struct.unpack(">H", data[offset:offset + 2])[0]
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if offset + 7 <= len(data):
                    height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                    return {"format": "jpeg", "width": width, "height": height}
                break
            offset += max(2, segment_length)
        return {"format": "jpeg"}
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return {"format": "webp"}
    return {"format": "unknown"}


def _remote_url(value: object) -> str:
    url = value.strip() if isinstance(value, str) else ""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urllib.parse.urlunparse(parsed)


def _vision_endpoint(args: dict[str, Any]) -> str:
    raw = args.get("endpointUrl") if isinstance(args.get("endpointUrl"), str) else os.environ.get(VISION_ENDPOINT_ENV)
    return raw.strip() if isinstance(raw, str) else ""


def _call_vision_endpoint(
    *,
    endpoint_url: str,
    prompt: str,
    image_url: str | None,
    image_path: str | None,
    image_bytes: bytes | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt": prompt,
        "imageUrl": image_url,
        "path": image_path,
        "metadata": metadata,
    }
    if image_bytes is not None:
        payload["imageBase64"] = base64.b64encode(image_bytes).decode("ascii")
    request = urllib.request.Request(
        endpoint_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    api_key = os.environ.get(VISION_API_KEY_ENV)
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(request, timeout=DEFAULT_VISION_TIMEOUT_SECONDS) as response:
        text = response.read(1024 * 1024).decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"analysis": text}
    return parsed if isinstance(parsed, dict) else {"analysis": parsed}


def vision_analyze(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    prompt = args.get("prompt").strip() if isinstance(args.get("prompt"), str) and args.get("prompt").strip() else "Describe the image."
    endpoint_url = _vision_endpoint(args)
    image_url = _remote_url(args.get("imageUrl"))
    image_bytes: bytes | None = None
    image_path: str | None = None
    metadata: dict[str, Any] = {}

    if isinstance(args.get("path"), str) and args.get("path").strip():
        path, data, error = _read_local_image(args["path"].strip(), context)
        if error:
            return {"error": error}
        assert path is not None and data is not None
        image_bytes = data
        image_path = path.as_posix()
        metadata = {
            **_image_metadata(data),
            "sizeBytes": len(data),
        }
    elif image_url:
        metadata = {"source": "url"}
    else:
        return {"error": "path or imageUrl is required"}

    if endpoint_url:
        try:
            analysis = _call_vision_endpoint(
                endpoint_url=endpoint_url,
                prompt=prompt,
                image_url=image_url or None,
                image_path=image_path,
                image_bytes=image_bytes,
                metadata=metadata,
            )
        except Exception as error:
            return {"error": f"vision endpoint failed: {error}", "metadata": metadata}
        return {
            "analysisAvailable": True,
            "provider": "http_endpoint",
            "metadata": metadata,
            "result": analysis,
        }

    return {
        "analysisAvailable": False,
        "metadata": metadata,
        "prompt": prompt,
        "hint": f"Set {VISION_ENDPOINT_ENV} or pass endpointUrl to enable semantic image analysis. Local image metadata was extracted without sending image bytes externally.",
    }


VISION_ANALYZE_TOOL_SPEC = ToolSpec(
    name="vision_analyze",
    display_name="Analyzing image",
    permission="ask",
    enabled=True,
    handler=vision_analyze,
    prompt_hint="Use for image files or image URLs when visual inspection is required. Without a configured vision endpoint, it returns local image metadata and a setup hint.",
    schema={
        "type": "function",
        "function": {
            "name": "vision_analyze",
            "description": "Analyze a workspace image file or image URL. If AMADEUS_VISION_ENDPOINT is configured, sends the image/prompt to that endpoint; otherwise returns safe local image metadata and a setup hint.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative local image path."},
                    "imageUrl": {"type": "string", "description": "Remote HTTP(S) image URL."},
                    "prompt": {"type": "string", "description": "Question or instruction for image analysis."},
                    "endpointUrl": {"type": "string", "description": f"Optional vision HTTP endpoint. Defaults to ${VISION_ENDPOINT_ENV}."},
                },
                "additionalProperties": False,
            },
        },
    },
)
