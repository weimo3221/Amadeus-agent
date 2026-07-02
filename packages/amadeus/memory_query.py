from __future__ import annotations

import re

import jieba


jieba.setLogLevel(30)

MAX_MEMORY_QUERY_TERMS = 24
MAX_MEMORY_ITEM_QUERY_TERMS = 12
STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "what",
    "when",
    "where",
    "which",
    "should",
    "about",
}
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_TOKEN_STRIP_RE = re.compile(r"^[\s\W_]+|[\s\W_]+$", re.UNICODE)


def make_fts_query(query: str) -> str:
    terms = memory_query_terms(query, max_terms=MAX_MEMORY_QUERY_TERMS)
    if not terms:
        return '""'
    return " OR ".join(f'"{term.replace(chr(34), " ").strip()}"' for term in terms if term)


def memory_item_query_terms(query: str) -> list[str]:
    return memory_query_terms(query, max_terms=MAX_MEMORY_ITEM_QUERY_TERMS)


def build_fts_index_content(content: str) -> str:
    normalized = normalize_query_text(content)
    if not normalized:
        return ""
    terms = memory_query_terms(normalized, max_terms=128, include_full_query=False)
    if not terms:
        return normalized
    return f"{normalized}\n{' '.join(terms)}"


def memory_query_terms(
    query: str,
    *,
    max_terms: int,
    include_full_query: bool = True,
) -> list[str]:
    normalized = normalize_query_text(query)
    if not normalized:
        return []

    terms: list[str] = []
    if include_full_query:
        _append_term(terms, normalized, max_terms=max_terms, allow_short=True)

    for token in jieba.lcut(normalized, cut_all=False):
        normalized_token = normalize_token(token)
        if not normalized_token:
            continue
        allow_short = contains_cjk(normalized_token) and len(normalized_token) >= 2
        _append_term(terms, normalized_token, max_terms=max_terms, allow_short=allow_short)
        if len(terms) >= max_terms:
            break

    if len(terms) < max_terms:
        for chunk in _CJK_RE.findall(normalized):
            for gram in cjk_ngrams(chunk):
                _append_term(terms, gram, max_terms=max_terms, allow_short=True)
                if len(terms) >= max_terms:
                    break
            if len(terms) >= max_terms:
                break

    return terms


def cjk_ngrams(text: str) -> list[str]:
    chars = [char for char in text if contains_cjk(char)]
    grams: list[str] = []
    for size in (2, 3):
        if len(chars) < size:
            continue
        for index in range(0, len(chars) - size + 1):
            gram = "".join(chars[index:index + size])
            if gram not in grams:
                grams.append(gram)
    return grams


def normalize_query_text(value: str) -> str:
    return " ".join(value.replace("\x00", " ").split()).strip()


def normalize_token(value: str) -> str:
    token = _TOKEN_STRIP_RE.sub("", value.replace("\x00", " ").strip()).lower()
    if not token or token in STOP_WORDS:
        return ""
    return token


def contains_cjk(value: str) -> bool:
    return bool(_CJK_RE.search(value))


def _append_term(terms: list[str], term: str, *, max_terms: int, allow_short: bool) -> None:
    normalized = normalize_token(term)
    if not normalized:
        return
    if not allow_short and len(normalized) < 3:
        return
    if normalized not in terms:
        terms.append(normalized)
    if len(terms) > max_terms:
        del terms[max_terms:]
