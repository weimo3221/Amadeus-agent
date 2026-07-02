from __future__ import annotations

import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.memory_query import build_fts_index_content, make_fts_query, memory_item_query_terms, memory_query_terms


class MemoryQueryTests(unittest.TestCase):
    def test_jieba_terms_include_chinese_words_and_ngrams(self) -> None:
        terms = memory_query_terms("中文分词怎么处理", max_terms=24)

        self.assertIn("中文分词怎么处理", terms)
        self.assertIn("中文", terms)
        self.assertIn("分词", terms)
        self.assertIn("处理", terms)
        self.assertIn("文分", terms)
        self.assertIn("中文分", terms)
        self.assertNotIn("词", terms)

    def test_mixed_language_terms_keep_english_and_chinese_tokens(self) -> None:
        terms = memory_query_terms("Bridge WebSocket 转发逻辑", max_terms=24)

        self.assertIn("bridge", terms)
        self.assertIn("websocket", terms)
        self.assertIn("转发", terms)
        self.assertIn("逻辑", terms)

    def test_fts_query_is_bounded_and_quoted(self) -> None:
        query = make_fts_query(" ".join(f"token{i}" for i in range(40)))

        self.assertLessEqual(query.count(" OR ") + 1, 24)
        self.assertIn('"token0', query)
        self.assertIn('"token22"', query)
        self.assertNotIn('"token23"', query)

    def test_index_content_keeps_original_text_and_expanded_terms(self) -> None:
        content = build_fts_index_content("中文分词怎么处理")

        self.assertTrue(content.startswith("中文分词怎么处理\n"))
        self.assertIn("中文", content)
        self.assertIn("分词", content)
        self.assertIn("处理", content)

    def test_memory_item_terms_reuse_chinese_tokenizer(self) -> None:
        terms = memory_item_query_terms("项目记忆检索怎么做")

        self.assertIn("项目", terms)
        self.assertIn("记忆", terms)
        self.assertIn("检索", terms)
        self.assertLessEqual(len(terms), 12)


if __name__ == "__main__":
    unittest.main()
