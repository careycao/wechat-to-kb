"""Integration tests for KBWriter filesystem behavior."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.kb_config import KBConfig  # noqa: E402
from common.kb_storage import KBWriter  # noqa: E402


def _make_kb(base: Path) -> KBConfig:
    return KBConfig(
        key="test",
        name="TestKB",
        path=base / "TestKB",
        description="测试知识库，关注效率工具。",
        category_keywords={"工具": ["工具", "效率"], "未分类": []},
        category_order=["工具", "未分类"],
    )


# ── URL index ────────────────────────────────────────────────────────────────


class UrlIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.kb = _make_kb(self.tmp)
        self.writer = KBWriter(self.kb)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _save(self, url: str, title: str = "测试文章") -> None:
        self.writer.save_stage({
            "title": title,
            "html": "<p>内容</p>",
            "plain_text": "内容",
            "url": url,
        })

    def test_save_stage_creates_url_index_file(self):
        self._save("https://mp.weixin.qq.com/s/abc123?from=timeline")
        self.assertTrue((self.kb.path / "_url_index.json").exists())

    def test_url_index_stores_normalized_key(self):
        self._save("https://mp.weixin.qq.com/s/abc123?from=timeline&chksm=xyz")
        index = json.loads((self.kb.path / "_url_index.json").read_text("utf-8"))
        self.assertIn("https://mp.weixin.qq.com/s/abc123", index)

    def test_already_exists_true_for_same_url_different_tracking_params(self):
        self._save("https://mp.weixin.qq.com/s/abc123?from=timeline")
        url2 = "https://mp.weixin.qq.com/s/abc123?utm_source=wechat&scene=21"
        self.assertTrue(self.writer.already_exists("测试文章", url=url2))

    def test_already_exists_false_for_unseen_url(self):
        self.assertFalse(
            self.writer.already_exists("新文章", url="https://mp.weixin.qq.com/s/brandnew")
        )

    def test_frontmatter_url_is_normalized(self):
        self._save("https://mp.weixin.qq.com/s/abc123?utm_source=wechat")
        stage_dir = self.kb.path / "_stage"
        md_files = list(stage_dir.glob("*.md"))
        self.assertTrue(md_files, "stage 目录应有 .md 文件")
        content = md_files[0].read_text("utf-8")
        self.assertIn("url: https://mp.weixin.qq.com/s/abc123", content)
        self.assertNotIn("utm_source", content)


# ── purpose.md ───────────────────────────────────────────────────────────────


class PurposeMdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.kb = _make_kb(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_purpose_md_created_on_init(self):
        KBWriter(self.kb)
        self.assertTrue((self.kb.path / "purpose.md").exists())

    def test_purpose_md_contains_kb_description(self):
        KBWriter(self.kb)
        content = (self.kb.path / "purpose.md").read_text("utf-8")
        self.assertIn("测试知识库", content)

    def test_purpose_md_not_overwritten_on_second_init(self):
        KBWriter(self.kb)
        (self.kb.path / "purpose.md").write_text("用户自定义内容", encoding="utf-8")
        KBWriter(self.kb)
        content = (self.kb.path / "purpose.md").read_text("utf-8")
        self.assertEqual(content, "用户自定义内容")


# ── score_kb + purpose.md ────────────────────────────────────────────────────


class ScoreKbWithPurposeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.kb = _make_kb(self.tmp)
        KBWriter(self.kb)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipUnless(
        __import__("importlib").util.find_spec("jieba") is not None,
        "需要 jieba",
    )
    def test_purpose_md_boosts_score(self):
        from common.kb_routing import score_kb

        (self.kb.path / "purpose.md").write_text(
            "# Purpose\n这个知识库关注效率工具和自动化。\n", encoding="utf-8"
        )
        score_with, _ = score_kb("效率工具使用指南", "效率", self.kb)

        (self.kb.path / "purpose.md").write_text("", encoding="utf-8")
        score_without, _ = score_kb("效率工具使用指南", "效率", self.kb)

        self.assertGreaterEqual(score_with, score_without)

    @unittest.skipUnless(
        __import__("importlib").util.find_spec("jieba") is not None,
        "需要 jieba",
    )
    def test_empty_purpose_md_does_not_break_scoring(self):
        from common.kb_routing import score_kb

        (self.kb.path / "purpose.md").write_text("", encoding="utf-8")
        score, _ = score_kb("工具推荐", "工具", self.kb)
        self.assertIsInstance(score, int)


# ── KB_ENRICH integration ────────────────────────────────────────────────────


class KBEnrichIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.kb = _make_kb(self.tmp)
        self.writer = KBWriter(self.kb)
        self.data = {
            "title": "测试文章",
            "html": "<p>测试正文内容</p>",
            "plain_text": "测试正文内容",
            "url": "https://example.com/article",
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_kb_enrich_disabled_by_default(self):
        env = {k: v for k, v in os.environ.items() if k != "KB_ENRICH"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("common.kb_enricher.enrich_with_llm") as mock_enrich:
                self.writer.save_stage(self.data)
                mock_enrich.assert_not_called()

    def test_kb_enrich_enabled_calls_enricher(self):
        enriched = (
            "---\ntitle: 测试文章\nurl: https://example.com/article\n"
            "date: 2026-04-30\nsummary: Mock 摘要。\n---\n\n# 测试文章\n\n内容"
        )
        with mock.patch.dict(os.environ, {"KB_ENRICH": "1"}):
            with mock.patch("common.kb_enricher.enrich_with_llm", return_value=enriched):
                _, md_path = self.writer.save_stage(self.data)

        content = md_path.read_text("utf-8")
        self.assertIn("summary: Mock 摘要", content)

    def test_kb_enrich_failure_preserves_original_md(self):
        with mock.patch.dict(os.environ, {"KB_ENRICH": "1"}):
            with mock.patch(
                "common.kb_enricher.enrich_with_llm",
                side_effect=RuntimeError("LLM 调用失败"),
            ):
                _, md_path = self.writer.save_stage(self.data)

        content = md_path.read_text("utf-8")
        self.assertIn("测试文章", content)
        self.assertIn("测试正文内容", content)


if __name__ == "__main__":
    unittest.main()
