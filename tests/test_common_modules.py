import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.kb_config import KBConfig  # noqa: E402
from common.kb_indexing import generate_readme  # noqa: E402
from common.text_processing import extract_summary, safe_filename  # noqa: E402


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
            name="AI_KnowBase",
            path=Path("/tmp/AI_KnowBase"),
            description="测试知识库。",
            category_keywords={"工具与方法": ["工具"], "未分类": []},
            category_order=["工具与方法", "未分类"],
        )
        rows = [
            {
                "category": "01-工具与方法",
                "title": "测试文章",
                "html_path": Path("/tmp/AI_KnowBase/01-工具与方法/测试文章.html"),
                "txt_path": Path("/tmp/AI_KnowBase/01-工具与方法/测试文章.txt"),
                "summary": "摘要",
                "keywords": "关键词",
                "original_url": "https://mp.weixin.qq.com/s/example",
            }
        ]

        readme = generate_readme(kb, rows)

        self.assertIn("[测试文章](https://mp.weixin.qq.com/s/example)", readme)
        self.assertIn("| 分类 | 编号 | 文章标题 | 核心观点简介 | 关键词 |", readme)


if __name__ == "__main__":
    unittest.main()
