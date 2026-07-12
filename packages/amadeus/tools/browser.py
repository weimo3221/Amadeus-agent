from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from amadeus.tools.base import ToolSpec


BROWSER_BACKEND_ENV = "AMADEUS_BROWSER_TOOLS_URL"
BROWSER_MCP_ENV = "AMADEUS_BROWSER_MCP_URL"
DEFAULT_BROWSER_TIMEOUT_SECONDS = 20.0


def _browser_timeout(context: Any = None) -> float:
    timeout_seconds = getattr(context, "timeout_seconds", None)
    if isinstance(timeout_seconds, int | float) and timeout_seconds > 0:
        return max(1.0, min(60.0, float(timeout_seconds)))
    return DEFAULT_BROWSER_TIMEOUT_SECONDS


def _clean_args(args: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in args.items()
        if key not in {"backendUrl", "mcpUrl", "timeoutSeconds"}
    }


def _browser_backend_url(args: dict[str, Any]) -> str:
    raw_url = args.get("backendUrl") if isinstance(args.get("backendUrl"), str) else os.environ.get(BROWSER_BACKEND_ENV)
    return raw_url.strip().rstrip("/") if isinstance(raw_url, str) else ""


def _browser_mcp_url(args: dict[str, Any]) -> str:
    raw_url = args.get("mcpUrl") if isinstance(args.get("mcpUrl"), str) else os.environ.get(BROWSER_MCP_ENV)
    return raw_url.strip() if isinstance(raw_url, str) else ""


def _call_http_browser_backend(tool_name: str, args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    backend_url = _browser_backend_url(args)
    if not backend_url:
        return {"error": f"browser backend is not configured; set {BROWSER_BACKEND_ENV} or pass backendUrl"}
    payload = json.dumps({
        "tool": tool_name,
        "args": _clean_args(args),
        "sessionId": getattr(context, "session_id", None),
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{backend_url}/tools/call",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_browser_timeout(context)) as response:
            body = response.read(1024 * 1024).decode("utf-8", errors="replace")
    except Exception as error:
        return {"error": f"browser backend call failed: {error}"}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {"error": "browser backend returned non-json response", "preview": body[:1000]}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def call_browser_tool(tool_name: str, args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    mcp_url = _browser_mcp_url(args)
    if mcp_url:
        from amadeus.mcp import McpServerConfig, call_mcp_tool

        try:
            return call_mcp_tool(
                McpServerConfig(name="browser", url=mcp_url, timeout_seconds=_browser_timeout(context)),
                tool_name,
                _clean_args(args),
                timeout_seconds=_browser_timeout(context),
            )
        except Exception as error:
            return {"error": f"browser MCP call failed: {error}"}
    return _call_http_browser_backend(tool_name, args, context)


def _handler(tool_name: str):
    def run(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
        return call_browser_tool(tool_name, args, context)

    return run


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    base_properties: dict[str, Any] = {
        "backendUrl": {
            "type": "string",
            "description": f"Optional HTTP browser backend URL. Defaults to ${BROWSER_BACKEND_ENV}.",
        },
        "mcpUrl": {
            "type": "string",
            "description": f"Optional MCP JSON-RPC browser server URL. Defaults to ${BROWSER_MCP_ENV}.",
        },
    }
    if properties:
        base_properties.update(properties)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": base_properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


def _spec(
    name: str,
    display_name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    permission: str = "ask",
) -> ToolSpec:
    return ToolSpec(
        name=name,
        display_name=display_name,
        permission=permission,
        enabled=False,
        handler=_handler(name),
        prompt_hint="Use browser tools when a configured browser backend or browser MCP server is available and interactive page state is needed.",
        schema=_schema(name, description, properties, required),
    )


BROWSER_TOOL_SPECS: tuple[ToolSpec, ...] = (
    _spec(
        "browser_navigate",
        "Navigating browser",
        "Navigate the configured browser page to a URL.",
        {"url": {"type": "string", "description": "URL to navigate to."}},
        ["url"],
    ),
    _spec(
        "browser_snapshot",
        "Reading browser snapshot",
        "Return a structured accessibility/page snapshot from the configured browser.",
        permission="allow",
    ),
    _spec(
        "browser_click",
        "Clicking browser element",
        "Click an element in the configured browser by ref/selector or coordinates.",
        {
            "ref": {"type": "string", "description": "Backend-specific element reference from browser_snapshot."},
            "selector": {"type": "string", "description": "CSS selector when supported by the backend."},
            "x": {"type": "number", "description": "Optional x coordinate."},
            "y": {"type": "number", "description": "Optional y coordinate."},
        },
    ),
    _spec(
        "browser_type",
        "Typing into browser",
        "Type text into the active page or a target element.",
        {
            "text": {"type": "string", "description": "Text to type."},
            "ref": {"type": "string", "description": "Backend-specific element reference."},
            "selector": {"type": "string", "description": "CSS selector when supported by the backend."},
            "submit": {"type": "boolean", "description": "Whether to submit/press Enter after typing."},
        },
        ["text"],
    ),
    _spec(
        "browser_scroll",
        "Scrolling browser",
        "Scroll the browser page or a target element.",
        {
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "description": "Scroll direction."},
            "amount": {"type": "number", "description": "Backend-specific scroll amount."},
            "ref": {"type": "string", "description": "Optional target element reference."},
        },
    ),
    _spec("browser_back", "Going back in browser", "Navigate back in browser history."),
    _spec(
        "browser_press",
        "Pressing browser key",
        "Press a key in the active browser page.",
        {"key": {"type": "string", "description": "Key name such as Enter, Escape, Tab, ArrowDown, or Ctrl+L."}},
        ["key"],
    ),
    _spec("browser_get_images", "Listing browser images", "Return visible image metadata from the browser page.", permission="allow"),
    _spec(
        "browser_vision",
        "Inspecting browser visually",
        "Ask the browser backend for a visual analysis of the current page.",
        {"prompt": {"type": "string", "description": "Question or instruction for visual page analysis."}},
        permission="ask",
    ),
    _spec("browser_console", "Reading browser console", "Return recent browser console messages.", permission="allow"),
    _spec(
        "browser_cdp",
        "Calling browser CDP",
        "Call a Chrome DevTools Protocol method through the configured browser backend.",
        {
            "method": {"type": "string", "description": "CDP method name."},
            "params": {"type": "object", "description": "CDP parameters."},
        },
        ["method"],
    ),
    _spec(
        "browser_dialog",
        "Handling browser dialog",
        "Accept, dismiss, or inspect a browser dialog.",
        {
            "action": {"type": "string", "enum": ["accept", "dismiss", "status"], "description": "Dialog action."},
            "promptText": {"type": "string", "description": "Optional text for prompt dialogs."},
        },
    ),
)
