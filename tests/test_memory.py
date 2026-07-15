from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.memory import MessageMemoryStore
from amadeus.memory_embeddings import MemoryEmbeddingBackfillService


class FakeTextEmbeddingProvider:
    provider = "fake_embedding"
    model_id = "fake-model"
    dimensions = 2

    def __init__(self, vectors: dict[str, list[float]] | None = None, *, available: bool = True) -> None:
        self.vectors = dict(vectors or {})
        self._available = available

    def available(self) -> bool:
        return self._available

    def encode_texts(self, texts: list[str] | tuple[str, ...]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            normalized = str(text)
            if "deployment target" in normalized or "semantic deployment" in normalized:
                vectors.append([1.0, 0.0])
            elif "concise updates" in normalized:
                vectors.append([0.0, 1.0])
            else:
                vectors.append(self.vectors.get(normalized, [0.5, 0.5]))
        return vectors


class MessageMemoryStoreTests(unittest.TestCase):
    def test_role_workspace_path_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            workspace_path = str(Path(tmpdir) / "workspace")
            role = memory.create_role("Workspace Role", workspace_path=workspace_path)
            session = memory.create_session(str(role["id"]))
            updated = memory.update_role(str(role["id"]), workspace_path="")

            self.assertEqual(role["workspacePath"], workspace_path)
            self.assertEqual(memory.role_workspace_path_for_session(str(session["id"])), "")
            self.assertEqual(updated["workspacePath"], "")

    def test_default_workspace_path_applies_to_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            default_workspace = str(Path(tmpdir) / "project")
            memory = MessageMemoryStore(
                Path(tmpdir) / "amadeus.sqlite",
                default_workspace_path=default_workspace,
            )
            default_role = next(role for role in memory.list_roles() if role["id"] == "amadeus")
            role = memory.create_role("Default Workspace Role")
            session = memory.create_session(str(role["id"]))
            updated = memory.update_role(str(role["id"]), workspace_path="")

            self.assertEqual(default_role["workspacePath"], default_workspace)
            self.assertEqual(role["workspacePath"], default_workspace)
            self.assertEqual(memory.role_workspace_path_for_session(str(session["id"])), default_workspace)
            self.assertEqual(updated["workspacePath"], default_workspace)

    def test_role_soul_is_seeded_and_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            role = memory.create_role("Xiao Ai", persona="Helpful desktop agent", style="brief")
            identity = memory.role_identity(str(role["id"]))
            updated = memory.update_role_identity(str(role["id"]), name="小艾", soul_text="You are 小艾. Be concise.")

            self.assertTrue(Path(str(identity["path"])).is_file())
            self.assertIn("Xiao Ai", identity["content"])
            self.assertEqual(updated["roleName"], "小艾")
            self.assertIn("You are 小艾", updated["content"])

    def test_stable_memory_is_scoped_to_session_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            role_a = memory.create_role("Role A")
            role_b = memory.create_role("Role B")
            session_a = memory.create_session(str(role_a["id"]))
            session_b = memory.create_session(str(role_b["id"]))

            memory.update_stable_memory("user", "add", content="Role A user memory.", session_id=str(session_a["id"]))
            memory.update_stable_memory("user", "add", content="Role B user memory.", session_id=str(session_b["id"]))

            self.assertIn("Role A user memory", memory.read_stable_memory("user", session_id=str(session_a["id"]))["content"])
            self.assertNotIn("Role B user memory", memory.read_stable_memory("user", session_id=str(session_a["id"]))["content"])
            self.assertIn("Role B user memory", memory.read_stable_memory("user", session_id=str(session_b["id"]))["content"])

    def test_conversation_summary_persists_and_loads_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "amadeus.sqlite"
            memory = MessageMemoryStore(database_path)
            memory.save("session-1", "user", "first message")
            first = memory.save_conversation_summary("session-1", "Initial summary")
            second = memory.save_conversation_summary(
                "session-1",
                "Updated summary",
                summarized_message_count=7,
                covered_message_count=5,
                source_message_start_id=2,
                source_message_end_id=9,
                covered_through_message_id=9,
                model="test-model",
            )

            reloaded = MessageMemoryStore(database_path).load_conversation_summary("session-1")

        self.assertIsNotNone(reloaded)
        assert reloaded is not None
        self.assertEqual(first["summaryId"] + 1, second["summaryId"])
        self.assertEqual(reloaded["summaryId"], second["summaryId"])
        self.assertEqual(reloaded["sessionId"], "session-1")
        self.assertEqual(reloaded["content"], "Updated summary")
        self.assertEqual(reloaded["charCount"], len("Updated summary"))
        self.assertEqual(reloaded["summarizedMessageCount"], 7)
        self.assertEqual(reloaded["coveredMessageCount"], 5)
        self.assertEqual(reloaded["sourceMessageStartId"], 2)
        self.assertEqual(reloaded["sourceMessageEndId"], 9)
        self.assertEqual(reloaded["coveredThroughMessageId"], 9)
        self.assertEqual(reloaded["model"], "test-model")
        self.assertIsInstance(reloaded["createdAt"], str)
        self.assertIsInstance(reloaded["updatedAt"], str)

    def test_conversation_summary_defaults_to_current_message_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save("session-1", "user", "hello")
            memory.save("session-1", "assistant", "hi")

            summary = memory.save_conversation_summary("session-1", "Two-message summary")

        self.assertEqual(summary["summarizedMessageCount"], 2)
        self.assertEqual(summary["coveredMessageCount"], 2)

    def test_load_can_filter_after_message_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            first_id = memory.save("session-1", "user", "old")
            memory.save("session-1", "assistant", "new")

            messages = memory.load("session-1", after_message_id=first_id)

        self.assertEqual([(message["role"], message["content"]) for message in messages], [("assistant", "new")])
        self.assertIsInstance(messages[0]["id"], int)
        self.assertIsInstance(messages[0]["createdAt"], str)

    def test_load_recent_turns_keeps_complete_user_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save("session-1", "user", "turn one")
            memory.save("session-1", "assistant", "answer one")
            memory.save("session-1", "user", "turn two")
            memory.save("session-1", "assistant", "", tool_calls=[{
                "id": "call_time",
                "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }])
            memory.save("session-1", "tool", '{"formatted": "12:00"}', tool_call_id="call_time", tool_name="get_current_time")
            memory.save("session-1", "assistant", "answer two")
            memory.save("session-1", "user", "turn three")

            messages = memory.load_recent_turns("session-1", 2)

        self.assertEqual(
            [(message["role"], message["content"]) for message in messages],
            [
                ("user", "turn two"),
                ("assistant", ""),
                ("tool", '{"formatted": "12:00"}'),
                ("assistant", "answer two"),
                ("user", "turn three"),
            ],
        )

    def test_reset_deletes_conversation_summary_for_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save_conversation_summary("session-1", "Summary to delete")
            memory.save_conversation_summary("session-2", "Summary to keep")

            memory.reset("session-1")

            self.assertIsNone(memory.load_conversation_summary("session-1"))
            self.assertEqual(memory.load_conversation_summary("session-2")["content"], "Summary to keep")  # type: ignore[index]

    def test_conversation_summary_rejects_empty_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")

            with self.assertRaises(ValueError):
                memory.save_conversation_summary("session-1", "  ")

    def test_tool_transcript_messages_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "amadeus.sqlite"
            memory = MessageMemoryStore(database_path)
            tool_calls = [{
                "id": "call_time",
                "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }]

            memory.save("session-1", "assistant", "", tool_calls=tool_calls)
            memory.save("session-1", "tool", '{"formatted": "12:00"}', tool_call_id="call_time", tool_name="get_current_time")
            reloaded = MessageMemoryStore(database_path).load("session-1")

        self.assertEqual(reloaded[0]["role"], "assistant")
        self.assertEqual(reloaded[0]["tool_calls"], tool_calls)
        self.assertEqual(reloaded[1]["role"], "tool")
        self.assertEqual(reloaded[1]["tool_call_id"], "call_time")
        self.assertEqual(reloaded[1]["tool_name"], "get_current_time")

    def test_memory_items_can_be_saved_listed_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            user_item = memory.save_memory_item(
                "user",
                "The user prefers concise Chinese updates.",
                confidence=0.9,
                source_session_id="session-1",
                source_message_id=12,
            )
            memory.save_memory_item("project", "The project uses Python-first runtime.", confidence=0.8)

            user_items = memory.list_memory_items(scope="user")
            queried_items = memory.list_memory_items(query="Python-first")
            deleted = memory.delete_memory_item(int(user_item["memoryItemId"]))
            active_items = memory.list_memory_items(scope="user")
            deleted_items = memory.list_memory_items(scope="user", include_deleted=True)

        self.assertEqual(len(user_items), 1)
        self.assertEqual(user_items[0]["content"], "The user prefers concise Chinese updates.")
        self.assertEqual(user_items[0]["confidence"], 0.9)
        self.assertEqual(user_items[0]["sourceSessionId"], "session-1")
        self.assertEqual(user_items[0]["sourceMessageId"], 12)
        self.assertEqual(len(queried_items), 1)
        self.assertEqual(queried_items[0]["scope"], "project")
        self.assertTrue(deleted)
        self.assertEqual(active_items, [])
        self.assertEqual(len(deleted_items), 1)
        self.assertTrue(deleted_items[0]["deleted"])

    def test_memory_items_have_mem0_like_fields_history_and_access_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            item = memory.save_memory_item(
                "user",
                "The user prefers careful status updates.",
                confidence=0.88,
                source_session_id="session-1",
                source_message_id=7,
                memory_type="preference",
                metadata={"tags": ["status"], "source": "explicit"},
                actor="test",
            )
            memory.record_memory_item_access([int(item["memoryItemId"])])
            accessed = memory.list_memory_items(memory_type="preference", query="status updates")[0]
            replaced = memory.replace_memory_item(
                int(item["memoryItemId"]),
                "The user prefers concise but careful status updates.",
                metadata={"tags": ["status", "tone"], "source": "correction"},
                actor="test",
            )
            assert replaced is not None
            deleted = memory.delete_memory_item(int(item["memoryItemId"]))
            history = memory.list_memory_item_history(int(item["memoryItemId"]))

        self.assertEqual(item["memoryType"], "preference")
        self.assertEqual(item["metadata"], {"source": "explicit", "tags": ["status"]})
        self.assertEqual(len(item["contentHash"]), 64)
        self.assertEqual(item["accessCount"], 0)
        self.assertEqual(accessed["accessCount"], 1)
        self.assertIsInstance(accessed["lastAccessedAt"], str)
        self.assertEqual(replaced["metadata"], {"source": "correction", "tags": ["status", "tone"]})
        self.assertTrue(deleted)
        self.assertEqual([event["event"] for event in history], ["DELETE", "UPDATE", "ADD"])
        self.assertEqual(history[0]["actor"], "runtime")
        self.assertEqual(history[1]["actor"], "test")
        self.assertEqual(history[2]["newMetadata"], {"source": "explicit", "tags": ["status"]})

    def test_memory_item_embeddings_track_coverage_and_stale_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            first = memory.save_memory_item("project", "The deployment target is local.", confidence=0.8)
            second = memory.save_memory_item("user", "The user prefers concise updates.", confidence=0.9)

            initial = memory.memory_item_embedding_coverage(provider="fake", model="fake-model", dimensions=2)
            memory.upsert_memory_item_embedding(
                int(first["memoryItemId"]),
                provider="fake",
                model="fake-model",
                dimensions=2,
                vector=[1.0, 0.0],
            )
            ready = memory.memory_item_embedding_coverage(provider="fake", model="fake-model", dimensions=2)
            needing = memory.list_memory_items_needing_embeddings(provider="fake", model="fake-model", dimensions=2)
            memory.replace_memory_item(int(first["memoryItemId"]), "The deployment target moved to desktop.")
            stale = memory.memory_item_embedding_coverage(provider="fake", model="fake-model", dimensions=2)

        self.assertEqual(initial["total"], 2)
        self.assertEqual(initial["ready"], 0)
        self.assertEqual(initial["missing"], 2)
        self.assertEqual(ready["ready"], 1)
        self.assertEqual(ready["missing"], 1)
        self.assertEqual([item["memoryItemId"] for item in needing], [second["memoryItemId"]])
        self.assertEqual(stale["ready"], 0)
        self.assertEqual(stale["stale"], 1)
        self.assertEqual(stale["missing"], 1)

    def test_memory_embedding_backfill_service_indexes_stale_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save_memory_item("project", "The deployment target is local.", confidence=0.8)
            memory.save_memory_item("user", "The user prefers concise updates.", confidence=0.9)
            service = MemoryEmbeddingBackfillService(memory, FakeTextEmbeddingProvider())

            result = service.backfill(limit=10, batch_size=1)
            coverage = memory.memory_item_embedding_coverage(provider="fake_embedding", model="fake-model", dimensions=2)

        self.assertEqual(result.error, "")
        self.assertEqual(result.scanned, 2)
        self.assertEqual(result.embedded, 2)
        self.assertEqual(coverage["ready"], 2)
        self.assertEqual(coverage["missing"], 0)

    def test_memory_items_hybrid_search_uses_vector_similarity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            target = memory.save_memory_item("project", "The deployment target is a local desktop app.", confidence=0.8)
            distractor = memory.save_memory_item("user", "The user prefers concise updates.", confidence=0.8)
            memory.upsert_memory_item_embedding(
                int(target["memoryItemId"]),
                provider="fake",
                model="fake-model",
                dimensions=2,
                vector=[1.0, 0.0],
            )
            memory.upsert_memory_item_embedding(
                int(distractor["memoryItemId"]),
                provider="fake",
                model="fake-model",
                dimensions=2,
                vector=[0.0, 1.0],
            )

            results = memory.search_memory_items_hybrid(
                query="semantic deployment alias",
                query_embedding=[1.0, 0.0],
                provider="fake",
                model="fake-model",
                dimensions=2,
                limit=2,
            )

        self.assertEqual(results[0]["memoryItemId"], target["memoryItemId"])
        self.assertEqual(results[0]["retrievalProvider"], "memory_items_hybrid")
        self.assertGreater(results[0]["vectorScore"], results[1]["vectorScore"])

    def test_memory_items_bm25_search_indexes_metadata_and_delete_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            item = memory.save_memory_item(
                "user",
                "The user prefers short status notes.",
                confidence=0.9,
                memory_type="preference",
                metadata={"source": "http", "tags": ["updates"], "nested": {"channel": "feishu"}},
            )

            metadata_results = memory.list_memory_items(
                query="http updates",
                metadata_filter={"source": "http", "tags": "updates"},
                limit=5,
            )
            nested_results = memory.list_memory_items(query="feishu", metadata_filter={"nested.channel": "feishu"})
            deleted = memory.delete_memory_item(int(item["memoryItemId"]))
            after_delete = memory.list_memory_items(query="http updates", limit=5)

        self.assertEqual(len(metadata_results), 1)
        self.assertEqual(metadata_results[0]["memoryItemId"], item["memoryItemId"])
        self.assertEqual(metadata_results[0]["retrievalProvider"], "memory_items_bm25")
        self.assertGreater(metadata_results[0]["bm25Score"], 0)
        self.assertEqual(len(nested_results), 1)
        self.assertTrue(deleted)
        self.assertEqual(after_delete, [])

    def test_memory_items_hybrid_search_unions_vector_and_bm25_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            vector_item = memory.save_memory_item("project", "The deployment target is a local desktop app.", confidence=0.8)
            bm25_item = memory.save_memory_item("project", "The release codename is heliotrope.", confidence=0.8)
            memory.upsert_memory_item_embedding(
                int(vector_item["memoryItemId"]),
                provider="fake",
                model="fake-model",
                dimensions=2,
                vector=[1.0, 0.0],
            )

            results = memory.search_memory_items_hybrid(
                query="heliotrope release",
                query_embedding=[1.0, 0.0],
                provider="fake",
                model="fake-model",
                dimensions=2,
                limit=3,
            )

        by_id = {int(item["memoryItemId"]): item for item in results}
        self.assertIn(int(vector_item["memoryItemId"]), by_id)
        self.assertIn(int(bm25_item["memoryItemId"]), by_id)
        self.assertGreater(by_id[int(bm25_item["memoryItemId"])]["bm25Score"], 0)
        self.assertEqual(by_id[int(vector_item["memoryItemId"])]["vectorScore"], 1.0)

    def test_memory_item_migration_preserves_legacy_rows_and_backfills_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "amadeus.sqlite"
            now = "2026-07-09T00:00:00+00:00"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE memory_items (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      scope TEXT NOT NULL CHECK (scope IN ('user', 'agent', 'project')),
                      content TEXT NOT NULL,
                      confidence REAL NOT NULL DEFAULT 1.0,
                      source_session_id TEXT,
                      source_message_id INTEGER,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL,
                      deleted_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO memory_items (
                      scope,
                      content,
                      confidence,
                      source_session_id,
                      source_message_id,
                      created_at,
                      updated_at
                    )
                    VALUES ('project', 'Legacy memory row survives migration.', 0.7, 'legacy-session', 9, ?, ?)
                    """,
                    (now, now),
                )

            memory = MessageMemoryStore(database_path)
            with sqlite3.connect(database_path) as connection:
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(memory_items)").fetchall()
                }
            items = memory.list_memory_items(scope="project", query="Legacy")
            history = memory.list_memory_item_history(int(items[0]["memoryItemId"]))

        self.assertIn("memory_type", columns)
        self.assertIn("metadata_json", columns)
        self.assertIn("content_hash", columns)
        self.assertIn("last_accessed_at", columns)
        self.assertIn("access_count", columns)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["memoryType"], "semantic")
        self.assertEqual(items[0]["metadata"], {})
        self.assertEqual(len(items[0]["contentHash"]), 64)
        self.assertEqual(history, [])

    def test_chinese_memory_search_uses_jieba_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save("session-1", "user", "中文的分词逻辑目前怎么做")

            results = memory.search("中文分词怎么处理", session_id="session-1", limit=5)

        self.assertEqual(len(results), 1)
        self.assertIn("中文的分词逻辑", results[0]["content"])

    def test_chinese_memory_search_respects_session_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save("session-1", "user", "中文分词召回只属于第一个会话")
            memory.save("session-2", "user", "中文分词召回属于第二个会话")

            scoped = memory.search("中文分词召回", session_id="session-1", limit=5)
            all_sessions = memory.search("中文分词召回", session_id=None, limit=5)

        self.assertEqual(len(scoped), 1)
        self.assertEqual(scoped[0]["sessionId"], "session-1")
        self.assertGreaterEqual(len(all_sessions), 2)
        self.assertEqual({result["sessionId"] for result in all_sessions}, {"session-1", "session-2"})

    def test_mixed_language_memory_search_matches_chinese_and_english_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save("session-1", "assistant", "计划系统需要 retry 和 heartbeat。")
            memory.save("session-1", "user", "Bridge 负责 WebSocket 转发，Agent 逻辑在 Python runtime。")

            results = memory.search("WebSocket 转发逻辑", session_id="session-1", limit=5)

        self.assertEqual(len(results), 1)
        self.assertIn("WebSocket 转发", results[0]["content"])

    def test_message_fts_rebuild_tokenizes_existing_chinese_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "amadeus.sqlite"
            memory = MessageMemoryStore(database_path)
            message_id = memory.save("session-1", "user", "中文的分词逻辑目前怎么做")
            with sqlite3.connect(database_path) as connection:
                connection.execute("DELETE FROM messages_fts")
                connection.execute(
                    """
                    INSERT INTO messages_fts(rowid, content, session_id, role, created_at)
                    SELECT id, content, session_id, role, created_at
                    FROM messages
                    """
                )

            self.assertEqual(memory.search("中文分词怎么处理", session_id="session-1"), [])

            rebuilt = MessageMemoryStore(database_path)
            results = rebuilt.search("中文分词怎么处理", session_id="session-1")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], message_id)

    def test_chinese_memory_items_use_jieba_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save_memory_item("project", "项目记忆检索应该支持中文分词召回。", confidence=0.9)

            items = memory.list_memory_items(query="中文分词怎么处理")

        self.assertEqual(len(items), 1)
        self.assertIn("中文分词召回", items[0]["content"])

    def test_chinese_memory_items_rank_by_confidence_after_token_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save_memory_item("project", "项目记忆检索可以用临时关键词兜底。", confidence=0.3)
            memory.save_memory_item("project", "项目记忆检索应该支持中文分词召回。", confidence=0.9)

            items = memory.list_memory_items(scope="project", query="记忆检索召回")

        self.assertGreaterEqual(len(items), 2)
        self.assertIn("中文分词召回", items[0]["content"])
        self.assertGreater(items[0]["confidence"], items[1]["confidence"])

    def test_memory_items_validate_scope_and_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")

            with self.assertRaises(ValueError):
                memory.save_memory_item("invalid", "fact")
            with self.assertRaises(ValueError):
                memory.save_memory_item("user", "fact", confidence=1.5)

    def test_memory_review_candidates_can_be_saved_listed_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            first = memory.save_memory_review_candidate(
                "session-1",
                "user",
                "The user prefers concise updates.",
                confidence=0.8,
                reason="User explicitly requested concise status updates.",
                scope_reason="This is a stable user preference.",
                safety_labels=["explicit", "non_secret", "correct scope", "explicit"],
                retention_type="stable_preference",
                source_message_start_id=2,
                source_message_end_id=4,
            )
            duplicate = memory.save_memory_review_candidate(
                "session-1",
                "user",
                "The user prefers concise updates.",
                confidence=0.6,
            )
            pending = memory.list_memory_review_candidates(session_id="session-1", status="pending")

        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["candidateId"], duplicate["candidateId"])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["scope"], "user")
        self.assertEqual(pending[0]["confidence"], 0.8)
        self.assertEqual(pending[0]["reason"], "User explicitly requested concise status updates.")
        self.assertEqual(pending[0]["scopeReason"], "This is a stable user preference.")
        self.assertEqual(pending[0]["safetyLabels"], ["explicit", "non_secret", "correct_scope"])
        self.assertEqual(pending[0]["retentionType"], "stable_preference")
        self.assertEqual(pending[0]["sourceMessageStartId"], 2)
        self.assertEqual(pending[0]["sourceMessageEndId"], 4)

    def test_memory_review_candidate_migration_preserves_legacy_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "amadeus.sqlite"
            now = "2026-06-21T00:00:00+00:00"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE memory_review_candidates (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      session_id TEXT NOT NULL,
                      scope TEXT NOT NULL CHECK (scope IN ('user', 'agent', 'project')),
                      content TEXT NOT NULL,
                      confidence REAL NOT NULL DEFAULT 0.7,
                      reason TEXT,
                      source_message_start_id INTEGER,
                      source_message_end_id INTEGER,
                      status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected', 'superseded')),
                      memory_item_id INTEGER,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO memory_review_candidates (
                      session_id,
                      scope,
                      content,
                      confidence,
                      reason,
                      source_message_start_id,
                      source_message_end_id,
                      status,
                      created_at,
                      updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        "legacy-session",
                        "user",
                        "The user prefers migration-safe memory.",
                        0.75,
                        "Legacy candidate reason.",
                        3,
                        5,
                        now,
                        now,
                    ),
                )

            memory = MessageMemoryStore(database_path)
            with sqlite3.connect(database_path) as connection:
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(memory_review_candidates)").fetchall()
                }
            candidates = memory.list_memory_review_candidates(session_id="legacy-session", status="pending")
            accepted = memory.accept_memory_review_candidate(int(candidates[0]["candidateId"]))
            items = memory.list_memory_items(scope="user")

        self.assertIn("scope_reason", columns)
        self.assertIn("safety_labels", columns)
        self.assertIn("retention_type", columns)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["content"], "The user prefers migration-safe memory.")
        self.assertEqual(candidates[0]["confidence"], 0.75)
        self.assertEqual(candidates[0]["reason"], "Legacy candidate reason.")
        self.assertEqual(candidates[0]["scopeReason"], "")
        self.assertEqual(candidates[0]["safetyLabels"], [])
        self.assertEqual(candidates[0]["retentionType"], "long_term")
        self.assertEqual(candidates[0]["sourceMessageStartId"], 3)
        self.assertEqual(candidates[0]["sourceMessageEndId"], 5)
        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["candidate"]["status"], "accepted")
        self.assertEqual(accepted["candidate"]["scopeReason"], "")
        self.assertEqual(accepted["candidate"]["safetyLabels"], [])
        self.assertEqual(accepted["candidate"]["retentionType"], "long_term")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], "The user prefers migration-safe memory.")

    def test_accept_memory_review_candidate_promotes_to_memory_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            candidate = memory.save_memory_review_candidate(
                "session-1",
                "project",
                "Amadeus uses Python-first runtime.",
                confidence=0.85,
                scope_reason="This is a durable project fact.",
                safety_labels=["explicit", "correct_scope"],
                retention_type="durable_project_fact",
                source_message_end_id=9,
            )
            result = memory.accept_memory_review_candidate(int(candidate["candidateId"]))
            accepted = memory.list_memory_review_candidates(status="accepted")
            items = memory.list_memory_items(scope="project")

        self.assertTrue(result["accepted"])
        self.assertFalse(result["duplicateMemoryItem"])
        self.assertEqual(result["candidate"]["status"], "accepted")
        self.assertEqual(result["candidate"]["scopeReason"], "This is a durable project fact.")
        self.assertEqual(result["candidate"]["safetyLabels"], ["explicit", "correct_scope"])
        self.assertEqual(result["candidate"]["retentionType"], "durable_project_fact")
        self.assertEqual(result["item"]["content"], "Amadeus uses Python-first runtime.")
        self.assertEqual(result["item"]["memoryType"], "project_fact")
        self.assertEqual(result["item"]["metadata"]["retentionType"], "durable_project_fact")  # type: ignore[index]
        self.assertEqual(result["item"]["metadata"]["source"], "memory_review")  # type: ignore[index]
        self.assertEqual(result["item"]["sourceSessionId"], "session-1")
        self.assertEqual(result["item"]["sourceMessageId"], 9)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["memoryItemId"], result["item"]["memoryItemId"])
        self.assertEqual(len(items), 1)

    def test_reject_memory_review_candidate_does_not_write_memory_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            candidate = memory.save_memory_review_candidate(
                "session-1",
                "agent",
                "Do not store transient task progress.",
                confidence=0.7,
            )
            result = memory.reject_memory_review_candidate(int(candidate["candidateId"]))
            rejected = memory.list_memory_review_candidates(status="rejected")
            items = memory.list_memory_items(scope="agent")

        self.assertTrue(result["rejected"])
        self.assertEqual(result["candidate"]["status"], "rejected")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(items, [])

    def test_rejected_memory_review_candidate_suppresses_same_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            candidate = memory.save_memory_review_candidate(
                "session-1",
                "agent",
                "Never store secrets in durable memory.",
                confidence=0.7,
            )
            memory.reject_memory_review_candidate(int(candidate["candidateId"]))
            repeated = memory.save_memory_review_candidate(
                "session-1",
                "agent",
                "Never store secrets in durable memory.",
                confidence=0.9,
            )
            pending = memory.list_memory_review_candidates(session_id="session-1", status="pending")
            rejected = memory.list_memory_review_candidates(session_id="session-1", status="rejected")

        self.assertTrue(repeated["duplicate"])
        self.assertTrue(repeated["suppressed"])
        self.assertEqual(repeated["status"], "rejected")
        self.assertEqual(pending, [])
        self.assertEqual(len(rejected), 1)

    def test_memory_review_jobs_can_be_recorded_and_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            job = memory.start_memory_review_job("session-1", "manual")
            finished = memory.finish_memory_review_job(
                int(job["jobId"]),
                "completed",
                source_message_start_id=2,
                source_message_end_id=5,
                source_message_count=4,
                proposed_candidate_count=3,
                saved_candidate_count=2,
                suppressed_candidate_count=1,
                duration_ms=123,
            )
            jobs = memory.list_memory_review_jobs(session_id="session-1")

        self.assertEqual(job["status"], "running")
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["trigger"], "manual")
        self.assertEqual(finished["sourceMessageStartId"], 2)
        self.assertEqual(finished["sourceMessageEndId"], 5)
        self.assertEqual(finished["sourceMessageCount"], 4)
        self.assertEqual(finished["proposedCandidateCount"], 3)
        self.assertEqual(finished["savedCandidateCount"], 2)
        self.assertEqual(finished["suppressedCandidateCount"], 1)
        self.assertEqual(finished["durationMs"], 123)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["jobId"], job["jobId"])

    def test_memory_review_jobs_record_skipped_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            job = memory.start_memory_review_job("session-1", "auto")
            finished = memory.finish_memory_review_job(int(job["jobId"]), "skipped", reason="below_threshold")
            skipped = memory.list_memory_review_jobs(status="skipped")

        self.assertEqual(finished["status"], "skipped")
        self.assertEqual(finished["reason"], "below_threshold")
        self.assertEqual(len(skipped), 1)

    def test_tasks_can_be_created_listed_cancelled_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            task = memory.create_task(
                session_id="session-1",
                title="Research task persistence",
                body="Check SQLite task storage.",
                priority=5,
            )
            listed = memory.list_tasks(session_id="session-1", active_only=True)
            cancelled = memory.cancel_task(str(task["id"]), reason="User stopped the task")
            events = memory.list_task_events(str(task["id"]))

        self.assertEqual(task["status"], "queued")
        self.assertEqual(listed["summary"]["queued"], 1)
        self.assertEqual(listed["tasks"][0]["title"], "Research task persistence")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["error"], "User stopped the task")
        self.assertEqual([event["type"] for event in events], ["created", "cancelled"])
        self.assertEqual(events[1]["metadata"], {"previousStatus": "queued"})

    def test_plan_item_status_can_be_updated_for_linked_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save_session_plan(
                "session-1",
                [
                    {"id": "inspect", "content": "Inspect task flow", "status": "in_progress"},
                    {"id": "implement", "content": "Implement task flow", "status": "pending"},
                ],
            )

            updated = memory.update_plan_item_status(
                session_id="session-1",
                plan_item_id="implement",
                status="in_progress",
            )

        statuses = {item["id"]: item["status"] for item in updated["items"]}
        self.assertEqual(statuses["inspect"], "pending")
        self.assertEqual(statuses["implement"], "in_progress")

    def test_plan_runs_persist_and_archive_by_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            user_message_id = memory.save("session-1", "user", "plan this")
            memory.save_session_plan(
                "session-1",
                [{"id": "inspect", "content": "Inspect", "status": "in_progress"}],
                turn_id="turn-1",
                user_message_id=user_message_id,
            )
            assistant_message_id = memory.save("session-1", "assistant", "done")
            archived = memory.finish_plan_run(
                session_id="session-1",
                turn_id="turn-1",
                assistant_message_id=assistant_message_id,
            )
            runs = memory.list_plan_runs(session_id="session-1")

        self.assertIsNotNone(archived)
        assert archived is not None
        self.assertEqual(archived["status"], "incomplete")
        self.assertEqual(runs["count"], 1)
        self.assertEqual(runs["planRuns"][0]["userMessageId"], user_message_id)
        self.assertEqual(runs["planRuns"][0]["assistantMessageId"], assistant_message_id)

    def test_task_can_block_resume_and_approve_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            task = memory.create_task(session_id="session-1", title="Review me", review_required=True)
            memory.start_task(str(task["id"]), claim_lock="worker-1")
            blocked = memory.block_task(
                str(task["id"]),
                claim_lock="worker-1",
                result="Draft",
                reason="Needs review",
                checkpoint={"status": "blocked", "phase": "approval_required", "reason": "human_review_required"},
                handoff_summary="Draft",
            )
            resumed = memory.resume_blocked_task(str(task["id"]))
            memory.start_task(str(task["id"]), claim_lock="worker-2")
            blocked_again = memory.block_task(str(task["id"]), claim_lock="worker-2", result="Draft 2", reason="Needs review")
            approved = memory.approve_task_review(str(task["id"]))
            events = memory.list_task_events(str(task["id"]))

        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["checkpoint"]["phase"], "approval_required")
        self.assertEqual(blocked["handoffSummary"], "Draft")
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["checkpoint"]["phase"], "approval_resume_requested")
        self.assertEqual(blocked_again["result"], "Draft 2")
        self.assertEqual(approved["status"], "succeeded")
        self.assertEqual(approved["checkpoint"]["phase"], "approved")
        self.assertIn("review_approved", [event["type"] for event in events])

    def test_resume_blocked_worker_action_preserves_action_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            task = memory.create_task(session_id="session-1", title="Approve action")
            memory.start_task(str(task["id"]), claim_lock="worker-1")
            memory.block_task(
                str(task["id"]),
                claim_lock="worker-1",
                reason="Needs action approval",
                checkpoint={
                    "status": "blocked",
                    "phase": "approval_required",
                    "reason": "worker_tool_permission_required",
                    "toolName": "process",
                    "approvalActionKey": "process:kill",
                    "approvalActionLabel": "process kill pid 123",
                    "approvalRiskLevel": "high",
                    "approvalRiskLabels": ["destructive", "process_signal"],
                },
            )

            resumed = memory.resume_blocked_task(str(task["id"]))

        self.assertEqual(resumed["checkpoint"]["phase"], "approval_resume_requested")
        self.assertEqual(resumed["checkpoint"]["approvedToolName"], "process")
        self.assertEqual(resumed["checkpoint"]["approvedToolAction"], "process:kill")
        self.assertEqual(resumed["checkpoint"]["approvedToolActions"], ["process:kill"])
        self.assertEqual(
            resumed["checkpoint"]["approvedToolActionExpirations"],
            {"process:kill": resumed["checkpoint"]["approvedToolActionExpiresAt"]},
        )
        expires_at = datetime.fromisoformat(str(resumed["checkpoint"]["approvedToolActionExpiresAt"]))
        self.assertGreater(expires_at, datetime.now(timezone.utc))
        self.assertEqual(resumed["checkpoint"]["resumeFrom"]["approvalActionLabel"], "process kill pid 123")
        self.assertEqual(resumed["checkpoint"]["resumeFrom"]["approvalRiskLabels"], ["destructive", "process_signal"])

    def test_task_graph_fields_edges_attempts_and_artifacts_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            root = memory.create_task(
                session_id="session-1",
                title="Root goal",
                worker_profile="orchestrator",
                acceptance_criteria=["All children complete"],
                context_hints={"workspace": "/tmp/project"},
                allowed_toolsets=["search"],
                disallowed_tools=["terminal"],
            )
            child = memory.create_task(
                session_id="session-1",
                title="Child work",
                parent_task_id=str(root["id"]),
                root_task_id=str(root["id"]),
                worker_profile="researcher",
                acceptance_criteria=["Find current task schema"],
            )
            edge = memory.add_task_edge(
                from_task_id=str(root["id"]),
                to_task_id=str(child["id"]),
                edge_type="blocks",
                metadata={"reason": "child waits for root in this test"},
            )
            attempt = memory.create_task_attempt(
                str(child["id"]),
                worker_id="worker-1",
                worker_profile="researcher",
                input_context={"task": "Child work"},
            )
            artifact = memory.add_task_artifact(
                str(child["id"]),
                {"type": "summary", "title": "Findings", "content": "Task graph storage works."},
                attempt_id=str(attempt["id"]),
                metadata={"source": "test"},
            )
            finished_attempt = memory.finish_task_attempt(
                str(attempt["id"]),
                status="succeeded",
                result="Done",
                token_usage={"total": 12},
            )
            graph = memory.get_task_graph(str(root["id"]))

        self.assertEqual(root["rootTaskId"], root["id"])
        self.assertEqual(root["workerProfile"], "orchestrator")
        self.assertEqual(root["acceptanceCriteria"], ["All children complete"])
        self.assertEqual(root["contextHints"], {"workspace": "/tmp/project"})
        self.assertEqual(root["allowedToolsets"], ["search"])
        self.assertEqual(root["disallowedTools"], ["terminal"])
        self.assertEqual(child["rootTaskId"], root["id"])
        self.assertEqual(edge["fromTaskId"], root["id"])
        self.assertEqual(edge["toTaskId"], child["id"])
        self.assertEqual(edge["metadata"], {"reason": "child waits for root in this test"})
        self.assertEqual(attempt["status"], "running")
        self.assertEqual(finished_attempt["status"], "succeeded")
        self.assertEqual(finished_attempt["tokenUsage"], {"total": 12})
        self.assertEqual(artifact["attemptId"], attempt["id"])
        self.assertEqual(artifact["content"], "Task graph storage works.")
        self.assertEqual(len(graph["tasks"]), 2)
        self.assertEqual(len(graph["edges"]), 1)

    def test_runnable_tasks_wait_for_dependency_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            dependency = memory.create_task(session_id="session-1", title="Dependency")
            child = memory.create_task(
                session_id="session-1",
                title="Dependent",
                parent_task_id=str(dependency["id"]),
                root_task_id=str(dependency["id"]),
            )
            independent = memory.create_task(session_id="session-1", title="Independent")
            memory.add_task_edge(from_task_id=str(dependency["id"]), to_task_id=str(child["id"]))

            before = memory.list_runnable_tasks(limit=10)
            memory.start_task(str(dependency["id"]), claim_lock="worker-1")
            memory.complete_task(str(dependency["id"]), claim_lock="worker-1", result="Ready")
            after = memory.list_runnable_tasks(limit=10)

        self.assertEqual({task["id"] for task in before}, {dependency["id"], independent["id"]})
        self.assertEqual({task["id"] for task in after}, {child["id"], independent["id"]})

    def test_task_worker_state_transitions_can_succeed_fail_and_ignore_finished_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            succeeded_task = memory.create_task(session_id="session-1", title="Succeed")
            running = memory.start_task(str(succeeded_task["id"]), claim_lock="worker-1")
            succeeded = memory.complete_task(str(succeeded_task["id"]), claim_lock="worker-1", result="Finished")
            unchanged = memory.cancel_task(str(succeeded_task["id"]), reason="Too late")

            failed_task = memory.create_task(session_id="session-1", title="Fail")
            memory.start_task(str(failed_task["id"]), claim_lock="worker-2")
            failed = memory.fail_task(str(failed_task["id"]), claim_lock="worker-2", error="Boom")
            events = memory.list_task_events(str(succeeded_task["id"]))

        self.assertEqual(running["status"], "running")
        self.assertIsNotNone(running["lastHeartbeat"])
        self.assertEqual(succeeded["status"], "succeeded")
        self.assertEqual(succeeded["result"], "Finished")
        self.assertEqual(unchanged["status"], "succeeded")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "Boom")
        self.assertEqual([event["type"] for event in events], ["created", "running", "succeeded"])

    def test_task_status_migration_maps_legacy_done_to_succeeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "amadeus.sqlite"
            now = "2026-06-30T00:00:00+00:00"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE tasks (
                      id TEXT PRIMARY KEY,
                      session_id TEXT NOT NULL,
                      title TEXT NOT NULL,
                      body TEXT NOT NULL DEFAULT '',
                      status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'blocked', 'done', 'failed', 'cancelled')),
                      priority INTEGER NOT NULL DEFAULT 0,
                      due_at TEXT,
                      claim_lock TEXT,
                      last_heartbeat TEXT,
                      result TEXT,
                      error TEXT,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL,
                      finished_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO tasks (
                      id, session_id, title, body, status, priority, created_at, updated_at, finished_at
                    )
                    VALUES ('task-legacy', 'session-1', 'Legacy done', '', 'done', 0, ?, ?, ?)
                    """,
                    (now, now, now),
                )

            memory = MessageMemoryStore(database_path)
            task = memory.get_task("task-legacy")
            listed = memory.list_tasks(session_id="session-1", status="succeeded")

        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(listed["summary"]["succeeded"], 1)

    def test_replace_memory_item_updates_active_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            item = memory.save_memory_item("project", "Old fact.", confidence=0.4)
            replaced = memory.replace_memory_item(
                int(item["memoryItemId"]),
                "New fact.",
                scope="user",
                confidence=0.8,
            )
            items = memory.list_memory_items(scope="user")

        self.assertIsNotNone(replaced)
        assert replaced is not None
        self.assertEqual(replaced["memoryItemId"], item["memoryItemId"])
        self.assertEqual(replaced["scope"], "user")
        self.assertEqual(replaced["content"], "New fact.")
        self.assertEqual(replaced["confidence"], 0.8)
        self.assertEqual(len(items), 1)

    def test_accept_memory_review_candidate_reuses_existing_memory_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            existing = memory.save_memory_item("user", "The user prefers Chinese responses.", confidence=1.0)
            candidate = memory.save_memory_review_candidate(
                "session-1",
                "user",
                "The user prefers Chinese responses.",
                confidence=0.6,
            )
            result = memory.accept_memory_review_candidate(int(candidate["candidateId"]))
            items = memory.list_memory_items(scope="user")

        self.assertTrue(result["accepted"])
        self.assertTrue(result["duplicateMemoryItem"])
        self.assertEqual(result["item"]["memoryItemId"], existing["memoryItemId"])
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
