from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.skills import SkillCatalog, parse_frontmatter


class SkillsCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.skills_root = Path(self.tmpdir.name) / "skills"
        runtime_debug = self.skills_root / "development" / "runtime-debug"
        runtime_debug.mkdir(parents=True)
        (runtime_debug / "SKILL.md").write_text(
            "\n".join([
                "---",
                "name: runtime-debug",
                "description: Debug runtime behavior.",
                "preferred_tools:",
                "  - search_files",
                "  - read_file",
                "allowed_tools: [search_files, read_file, patch]",
                "---",
                "",
                "# Runtime Debug",
                "",
                "Use tests before fixes.",
            ]),
            encoding="utf-8",
        )
        desktop_e2e = self.skills_root / "development" / "desktop-e2e"
        desktop_e2e.mkdir(parents=True)
        (desktop_e2e / "SKILL.md").write_text(
            "---\nname: desktop-e2e\ndescription: Extend packaged Electron coverage.\n---\n\nAssert visible behavior.\n",
            encoding="utf-8",
        )
        self.catalog = SkillCatalog(self.skills_root)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_parse_frontmatter_supports_lists(self) -> None:
        metadata, body = parse_frontmatter(
            "---\nname: demo\ntools:\n  - read_file\n  - patch\n---\n\nBody.\n",
        )

        self.assertEqual(metadata["name"], "demo")
        self.assertEqual(metadata["tools"], ["read_file", "patch"])
        self.assertEqual(body, "Body.")

    def test_list_skills_returns_metadata(self) -> None:
        skills = self.catalog.skill_summaries()

        self.assertEqual(len(skills), 2)
        self.assertEqual(skills[0]["identifier"], "development/desktop-e2e")
        self.assertEqual(skills[1]["identifier"], "development/runtime-debug")

    def test_view_skill_accepts_identifier_and_slug(self) -> None:
        by_identifier = self.catalog.view_skill("development/runtime-debug")
        by_slug = self.catalog.view_skill("runtime-debug")

        self.assertIsNotNone(by_identifier)
        self.assertEqual(by_identifier["name"], "runtime-debug")
        self.assertIn("Use tests before fixes.", by_identifier["instructions"])
        self.assertEqual(by_slug["identifier"], "development/runtime-debug")

    def test_build_prompt_block_surfaces_selected_skills(self) -> None:
        block, resolved = self.catalog.build_prompt_block(["desktop-e2e", "runtime-debug"])

        self.assertTrue(resolved.ok)
        self.assertIn("<active-skills>", block)
        self.assertIn("development/desktop-e2e", block)
        self.assertIn("preferred_tools: search_files, read_file", block)

    def test_build_prompt_block_reports_missing_skills(self) -> None:
        block, resolved = self.catalog.build_prompt_block(["missing-skill"])

        self.assertEqual(block, "")
        self.assertFalse(resolved.ok)
        self.assertEqual(resolved.missing, ("missing-skill",))


class SkillsToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.skills_root = Path(self.tmpdir.name) / "skills"
        skill_dir = self.skills_root / "development" / "runtime-debug"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: runtime-debug\ndescription: Debug runtime behavior.\n---\n\nUse evidence.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_skills_tools_use_catalog(self) -> None:
        from amadeus.tools import skills as skills_tool_module

        original_catalog = skills_tool_module.SkillCatalog
        try:
            skills_tool_module.SkillCatalog = lambda: SkillCatalog(self.skills_root)
            listed = skills_tool_module.skills_list({})
            viewed = skills_tool_module.skill_view({"name": "runtime-debug"})
        finally:
            skills_tool_module.SkillCatalog = original_catalog

        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["skills"][0]["identifier"], "development/runtime-debug")
        self.assertEqual(viewed["name"], "runtime-debug")
        self.assertIn("Use evidence.", viewed["instructions"])

    def test_skill_view_reports_missing_name(self) -> None:
        from amadeus.tools.skills import skill_view

        result = skill_view({})

        self.assertEqual(result, {"error": "name must be a non-empty string"})
