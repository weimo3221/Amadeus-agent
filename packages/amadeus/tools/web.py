from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int


DEFAULT_WEB_TIMEOUT_SECONDS = 12
MAX_WEB_TIMEOUT_SECONDS = 30
DEFAULT_SEARCH_RESULTS = 5
MAX_SEARCH_RESULTS = 10
DEFAULT_EXTRACT_CHARS = 12000
MAX_EXTRACT_CHARS = 30000
MAX_EXTRACT_URLS = 5
MAX_FETCH_BYTES = 1024 * 1024
USER_AGENT = "Amadeus-Agent/1.0 (+https://localhost)"


def _timeout(args: dict[str, Any], fallback: int = DEFAULT_WEB_TIMEOUT_SECONDS) -> int:
    return normalize_positive_int(args.get("timeoutSeconds"), fallback, 1, MAX_WEB_TIMEOUT_SECONDS)


def _require_http_url(value: object) -> str:
    url = value.strip() if isinstance(value, str) else ""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute http(s) URL")
    return urllib.parse.urlunparse(parsed)


def _fetch_url(url: str, *, timeout_seconds: int, max_bytes: int = MAX_FETCH_BYTES) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        raw = raw[:max_bytes]
        headers = getattr(response, "headers", {}) or {}
        content_type = ""
        if hasattr(headers, "get_content_type"):
            content_type = headers.get_content_type()
        elif hasattr(headers, "get"):
            content_type = str(headers.get("content-type") or headers.get("Content-Type") or "")
        charset = "utf-8"
        if hasattr(headers, "get_content_charset"):
            charset = headers.get_content_charset() or charset
        text = raw.decode(charset, errors="replace")
        return {
            "url": url,
            "finalUrl": response.geturl() if hasattr(response, "geturl") else url,
            "status": getattr(response, "status", None),
            "contentType": content_type,
            "bytesRead": len(raw),
            "truncatedByBytes": truncated,
            "text": text,
        }


class DuckDuckGoResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_link = False
        self._href = ""
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_dict = {name: value or "" for name, value in attrs}
        class_name = attrs_dict.get("class", "")
        href = attrs_dict.get("href", "")
        if "result__a" not in class_name or not href:
            return
        self._in_link = True
        self._href = href
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_link:
            return
        title = html.unescape(" ".join("".join(self._text_parts).split()))
        url = _decode_duckduckgo_url(self._href)
        if title and url:
            self.results.append({"title": title, "url": url})
        self._in_link = False
        self._href = ""
        self._text_parts = []


def _decode_duckduckgo_url(href: str) -> str:
    parsed = urllib.parse.urlparse(html.unescape(href))
    if parsed.scheme in {"http", "https"}:
        return urllib.parse.urlunparse(parsed)
    query = urllib.parse.parse_qs(parsed.query)
    uddg = query.get("uddg")
    if uddg and uddg[0]:
        return urllib.parse.unquote(uddg[0])
    return href


class TextExtractingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.description = ""
        self.text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if normalized == "title":
            self._in_title = True
            return
        if normalized == "meta":
            attrs_dict = {name.lower(): value or "" for name, value in attrs}
            name = attrs_dict.get("name", "").lower() or attrs_dict.get("property", "").lower()
            if name in {"description", "og:description"} and attrs_dict.get("content") and not self.description:
                self.description = attrs_dict["content"].strip()
            return
        if normalized in {"p", "div", "br", "li", "section", "article", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if normalized == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        else:
            self.text_parts.append(text)

    def extracted(self) -> dict[str, str]:
        title = " ".join(" ".join(self.title_parts).split())
        body = "\n".join(
            line
            for line in re.sub(r"[ \t\r\f\v]+", " ", "\n".join(self.text_parts)).splitlines()
            if line.strip()
        )
        return {
            "title": html.unescape(title),
            "description": html.unescape(" ".join(self.description.split())),
            "text": html.unescape(body),
        }


def web_search(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    _ = context
    query = args.get("query").strip() if isinstance(args.get("query"), str) else ""
    if not query:
        return {"error": "query is required"}
    max_results = normalize_positive_int(args.get("maxResults") or args.get("limit"), DEFAULT_SEARCH_RESULTS, 1, MAX_SEARCH_RESULTS)
    timeout_seconds = _timeout(args)
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})

    try:
        fetched = _fetch_url(url, timeout_seconds=timeout_seconds)
    except Exception as error:
        return {"error": f"web search failed: {error}"}

    parser = DuckDuckGoResultParser()
    parser.feed(str(fetched.get("text") or ""))
    results = parser.results[:max_results]
    return {
        "query": query,
        "provider": "duckduckgo_html",
        "resultCount": len(results),
        "results": results,
    }


def _extract_text_from_fetched(fetched: dict[str, Any], max_chars: int) -> dict[str, Any]:
    raw_text = str(fetched.get("text") or "")
    content_type = str(fetched.get("contentType") or "").lower()
    if "html" in content_type or "<html" in raw_text[:1000].lower():
        parser = TextExtractingHTMLParser()
        parser.feed(raw_text)
        extracted = parser.extracted()
    else:
        extracted = {"title": "", "description": "", "text": raw_text}

    text = extracted["text"]
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return {
        "url": fetched.get("url"),
        "finalUrl": fetched.get("finalUrl"),
        "status": fetched.get("status"),
        "contentType": fetched.get("contentType"),
        "title": extracted["title"],
        "description": extracted["description"],
        "text": text,
        "charCount": len(extracted["text"]),
        "truncated": truncated or bool(fetched.get("truncatedByBytes")),
    }


def web_extract(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    _ = context
    raw_urls = args.get("urls")
    if raw_urls is None and args.get("url") is not None:
        raw_urls = [args.get("url")]
    if not isinstance(raw_urls, list) or not raw_urls:
        return {"error": "url or urls is required"}

    try:
        urls = [_require_http_url(url) for url in raw_urls[:MAX_EXTRACT_URLS]]
    except ValueError as error:
        return {"error": str(error)}

    timeout_seconds = _timeout(args)
    max_chars = normalize_positive_int(args.get("maxChars"), DEFAULT_EXTRACT_CHARS, 500, MAX_EXTRACT_CHARS)
    pages: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for url in urls:
        try:
            pages.append(_extract_text_from_fetched(_fetch_url(url, timeout_seconds=timeout_seconds), max_chars))
        except Exception as error:
            errors.append({"url": url, "error": str(error)})

    return {
        "requestedCount": len(raw_urls),
        "fetchedCount": len(pages),
        "maxChars": max_chars,
        "pages": pages,
        "errors": errors,
    }


WEB_SEARCH_TOOL_SPEC = ToolSpec(
    name="web_search",
    display_name="Searching the web",
    permission="allow",
    enabled=True,
    handler=web_search,
    prompt_hint="Use for current public web information or documentation that is not available in the workspace.",
    schema={
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web through a lightweight HTML search provider and return titles and URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "maxResults": {"type": "number", "description": "Maximum search results. Defaults to 5 and is capped at 10."},
                    "timeoutSeconds": {"type": "number", "description": "HTTP timeout in seconds. Defaults to 12 and is capped at 30."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
)


WEB_EXTRACT_TOOL_SPEC = ToolSpec(
    name="web_extract",
    display_name="Extracting web page text",
    permission="ask",
    enabled=True,
    handler=web_extract,
    prompt_hint="Use after web_search or when the user provides URLs and page text is needed.",
    schema={
        "type": "function",
        "function": {
            "name": "web_extract",
            "description": "Fetch one or more HTTP(S) URLs and extract bounded readable text from HTML or text responses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Single HTTP(S) URL to fetch."},
                    "urls": {"type": "array", "items": {"type": "string"}, "description": "HTTP(S) URLs to fetch. At most 5 are processed."},
                    "maxChars": {"type": "number", "description": "Maximum extracted characters per page. Defaults to 12000 and is capped at 30000."},
                    "timeoutSeconds": {"type": "number", "description": "HTTP timeout in seconds. Defaults to 12 and is capped at 30."},
                },
                "additionalProperties": False,
            },
        },
    },
)
