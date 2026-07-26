import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.kb_config import KBConfig  # noqa: E402
from common.kb_enricher import _append_wikilinks_section, _inject_enrichment  # noqa: E402
from common.kb_indexing import generate_readme  # noqa: E402
from common.text_processing import extract_summary, safe_filename  # noqa: E402
from common.url_utils import normalize_url  # noqa: E402


class CommonModulesTest(unittest.TestCase):
    def test_safe_filename_replaces_reserved_characters(self):
        self.assertEqual(
            safe_filename('A/B:C*D?"E<F>G|H'),
            "A_B_C_D__E_F_G_H",
        )

    def test_extract_summary_uses_first_sentences(self):
        text = (
            "第一句足够长，能够进入摘要。"
            "第二句也足够长，能够进入摘要。"
            "第三句虽然存在，但不应该进入摘要。"
        )

        summary = extract_summary(text, max_sentences=2, max_len=80)

        self.assertEqual(summary, "第一句足够长，能够进入摘要。第二句也足够长，能够进入摘要。")

    def test_generate_readme_prefers_original_url_link(self):
        kb = KBConfig(
            key="ai",
            name="01-AI_KnowBase",
            path=Path("/tmp/01-AI_KnowBase"),
            description="测试知识库。",
            category_keywords={"工具与方法": ["工具"], "未分类": []},
            category_order=["工具与方法", "未分类"],
        )
        rows = [
            {
                "category": "01-工具与方法",
                "title": "测试文章",
                "html_path": Path("/tmp/01-AI_KnowBase/01-工具与方法/测试文章.html"),
                "txt_path": Path("/tmp/01-AI_KnowBase/01-工具与方法/测试文章.txt"),
                "summary": "摘要",
                "keywords": "关键词",
                "original_url": "https://mp.weixin.qq.com/s/example",
            }
        ]

        readme = generate_readme(kb, rows)

        self.assertIn("[测试文章](https://mp.weixin.qq.com/s/example)", readme)
        self.assertIn("| 分类 | 编号 | 文章标题 | 核心观点简介 | 关键词 |", readme)


class KBEnricherInjectTest(unittest.TestCase):
    def test_injects_summary_into_existing_frontmatter(self):
        md = "---\ntitle: 测试\nurl: https://example.com\n---\n\n# 正文"
        result = _inject_enrichment(md, {"summary": "这是摘要。"})
        self.assertIn("summary: 这是摘要。", result)
        self.assertIn("title: 测试", result)
        self.assertIn("# 正文", result)

    def test_injects_concepts_as_yaml_list(self):
        md = "---\ntitle: 测试\n---\n\n# 正文"
        result = _inject_enrichment(md, {"concepts": ["概念A", "概念B"]})
        self.assertIn("  - 概念A", result)
        self.assertIn("  - 概念B", result)

    def test_injects_related_as_wikilink_yaml_list(self):
        # related 字段以 [[wikilink]] 形式写入，兼容 Obsidian Graph View
        md = "---\ntitle: 测试\n---\n\n# 正文"
        result = _inject_enrichment(md, {"related": ["文章1", "文章2"]})
        self.assertIn('  - "[[文章1]]"', result)
        self.assertIn('  - "[[文章2]]"', result)

    def test_empty_enrichment_returns_original(self):
        md = "---\ntitle: 测试\n---\n\n# 正文"
        result = _inject_enrichment(md, {})
        self.assertEqual(result, md)

    def test_no_frontmatter_prepends_new_block(self):
        md = "# 没有 frontmatter 的正文"
        result = _inject_enrichment(md, {"summary": "摘要"})
        self.assertTrue(result.startswith("---\n"))
        self.assertIn("summary: 摘要", result)

    def test_append_wikilinks_section_generates_obsidian_links(self):
        md = "---\ntitle: 测试\n---\n\n# 正文"
        result = _append_wikilinks_section(md, ["文章A", "文章B"])
        self.assertIn("[[文章A]]", result)
        self.assertIn("[[文章B]]", result)
        self.assertIn("## 相关文章", result)

    def test_append_wikilinks_section_empty_list_unchanged(self):
        md = "# 正文"
        self.assertEqual(_append_wikilinks_section(md, []), md)


class UrlNormalizationTest(unittest.TestCase):
    def test_removes_utm_params(self):
        url = "https://example.com/article?utm_source=wechat&utm_medium=social&id=123"
        self.assertEqual(normalize_url(url), "https://example.com/article?id=123")

    def test_removes_wechat_tracking_params(self):
        url = "https://mp.weixin.qq.com/s/abc123?from=timeline&chksm=xyz&scene=21"
        self.assertEqual(normalize_url(url), "https://mp.weixin.qq.com/s/abc123")

    def test_wechat_keeps_only_article_id(self):
        url = "https://mp.weixin.qq.com/s/abc123def?utm_source=wechat&chksm=foo"
        self.assertEqual(normalize_url(url), "https://mp.weixin.qq.com/s/abc123def")

    def test_removes_fragment(self):
        url = "https://example.com/article#section1"
        self.assertEqual(normalize_url(url), "https://example.com/article")

    def test_unifies_scheme_to_https(self):
        url = "http://example.com/article"
        self.assertEqual(normalize_url(url), "https://example.com/article")

    def test_removes_trailing_slash(self):
        url = "https://example.com/article/"
        self.assertEqual(normalize_url(url), "https://example.com/article")

    def test_preserves_root_slash(self):
        url = "https://example.com/"
        self.assertEqual(normalize_url(url), "https://example.com/")

    def test_same_wechat_url_with_different_utm_normalizes_equal(self):
        url1 = "https://mp.weixin.qq.com/s/abc123?from=timeline"
        url2 = "https://mp.weixin.qq.com/s/abc123?utm_source=wechat"
        self.assertEqual(normalize_url(url1), normalize_url(url2))

    def test_empty_string_returns_empty(self):
        self.assertEqual(normalize_url(""), "")


if __name__ == "__main__":
    unittest.main()
