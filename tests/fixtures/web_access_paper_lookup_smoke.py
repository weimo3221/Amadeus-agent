from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


EXPECTED_TITLE = "Attention Is All You Need"
EXPECTED_ARXIV_ID = "1706.03762"
EXPECTED_AUTHOR = "Ashish Vaswani"


def normalize(value: object) -> str:
    return " ".join(str(value or "").split())


def curl(args: list[str]) -> str:
    completed = subprocess.run(
        ["curl", "-sS", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr or f"curl failed: {completed.returncode}")
    return completed.stdout


def load_json(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid JSON from proxy: {raw}") from error
    if not isinstance(parsed, dict):
        raise SystemExit(f"expected JSON object from proxy: {raw}")
    return parsed


def find_paper_from_arxiv_api() -> dict[str, object]:
    api_url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({
        "search_query": 'ti:"Attention Is All You Need"',
        "start": "0",
        "max_results": "5",
    })
    request = urllib.request.Request(api_url, headers={"User-Agent": "Amadeus-Agent/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        feed = response.read()

    root = ET.fromstring(feed)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries: list[dict[str, object]] = []
    for entry in root.findall("atom:entry", ns):
        raw_id = normalize(entry.findtext("atom:id", default="", namespaces=ns))
        arxiv_id = re.sub(r"v\d+$", "", raw_id.rsplit("/", 1)[-1])
        title = normalize(entry.findtext("atom:title", default="", namespaces=ns))
        authors = [
            normalize(author.findtext("atom:name", default="", namespaces=ns))
            for author in entry.findall("atom:author", ns)
        ]
        entries.append({
            "arxivId": arxiv_id,
            "rawId": raw_id,
            "title": title,
            "authors": authors,
        })

    paper = next(
        (
            entry
            for entry in entries
            if entry["title"].casefold() == EXPECTED_TITLE.casefold()
            and entry["arxivId"] == EXPECTED_ARXIV_ID
        ),
        None,
    )
    if paper is None:
        raise SystemExit(f"target paper not found in arXiv API results: {entries}")
    authors = paper.get("authors")
    if not isinstance(authors, list) or EXPECTED_AUTHOR not in authors:
        raise SystemExit(f"expected author missing from API result: {authors}")
    return paper


def verify_abstract_page(abs_url: str) -> dict[str, object]:
    created = load_json(curl([
        "-m",
        "45",
        "-X",
        "POST",
        "--data-raw",
        abs_url,
        "http://localhost:3456/new",
    ]))
    target = created.get("targetId") or created.get("id")
    if not isinstance(target, str) or not target:
        raise SystemExit(f"missing target id: {created}")

    try:
        page: dict[str, object] = {}
        extract_script = """
(() => ({
  pageTitle: document.title,
  heading: document.querySelector('h1.title')?.textContent.trim() || '',
  authors: [...document.querySelectorAll('.authors a')].map(a => a.textContent.trim()),
  submitted: document.body ? (document.body.innerText.match(/\\[Submitted[^\\n]+/) || [''])[0] : '',
  abstractPreview: document.querySelector('blockquote.abstract')?.textContent.replace(/\\s+/g, ' ').trim().slice(0, 240) || ''
}))()
""".strip()
        for _ in range(15):
            extracted = load_json(curl([
                "-m",
                "45",
                "-X",
                "POST",
                "--data-raw",
                extract_script,
                f"http://localhost:3456/eval?target={target}",
            ]))
            value = extracted.get("value")
            if not isinstance(value, dict):
                raise SystemExit(f"unexpected eval result: {extracted}")
            page = value
            page_blob = json.dumps(page, ensure_ascii=False)
            if EXPECTED_TITLE in page_blob and EXPECTED_AUTHOR in page_blob:
                break
            time.sleep(1)
        page_blob = json.dumps(page, ensure_ascii=False)
        if EXPECTED_TITLE not in page_blob:
            raise SystemExit(f"paper title missing from abstract page: {page}")
        if EXPECTED_AUTHOR not in page_blob:
            raise SystemExit(f"paper author missing from abstract page: {page}")
        return page
    finally:
        curl(["-m", "20", f"http://localhost:3456/close?target={target}"])


def main() -> None:
    paper = find_paper_from_arxiv_api()
    abs_url = f"https://arxiv.org/abs/{paper['arxivId']}"
    page = verify_abstract_page(abs_url)
    authors = paper.get("authors")
    assert isinstance(authors, list)
    print("AMADEUS_PAPER_LOOKUP_RESULT=" + json.dumps({
        "query": EXPECTED_TITLE,
        "arxivId": paper["arxivId"],
        "title": paper["title"],
        "authors": authors[:8],
        "sourceUrl": abs_url,
        "pageTitle": page.get("pageTitle"),
        "submitted": page.get("submitted"),
        "abstractPreview": page.get("abstractPreview"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
