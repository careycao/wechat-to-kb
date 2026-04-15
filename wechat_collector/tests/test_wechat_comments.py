import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.path_utils import redact_url_for_log  # noqa: E402
from wechat_collector.wechat_comments import (  # noqa: E402
    CommentContext,
    build_comments_api_url,
    extract_comment_context,
    fetch_comments_for_page,
    normalize_comment_payload,
    render_comments_html,
    render_comments_text,
)


class WeChatCommentsTest(unittest.TestCase):
    def test_extract_comment_context_uses_page_and_url_signals(self):
        article_url = (
            "https://mp.weixin.qq.com/s?"
            "__biz=MzA3MDAwMDAwMA==&mid=2650000001&idx=2&sn=abc"
        )
        page_html = """
        <html>
          <script>
            var comment_id = "1234567890";
            var appmsgid = "2650000009";
          </script>
        </html>
        """

        context = extract_comment_context(page_html, article_url)

        self.assertEqual(
            context,
            CommentContext(
                biz="MzA3MDAwMDAwMA==",
                appmsgid="2650000009",
                idx="2",
                comment_id="1234567890",
            ),
        )

    def test_extract_comment_context_supports_jsdecode_comment_id(self):
        article_url = "https://mp.weixin.qq.com/s/example"
        page_html = """
        <script>
            var biz = "MzA3MDAwMDAwMA==";
            var mid = "2650000001";
            var idx = "1";
            comment_id: JsDecode('4470607421891100678'),
        </script>
        """

        context = extract_comment_context(page_html, article_url)

        self.assertEqual(
            context,
            CommentContext(
                biz="MzA3MDAwMDAwMA==",
                appmsgid="2650000001",
                idx="1",
                comment_id="4470607421891100678",
            ),
        )

    def test_build_comments_api_url_contains_expected_query(self):
        context = CommentContext(
            biz="MzA3MDAwMDAwMA==",
            appmsgid="2650000009",
            idx="2",
            comment_id="1234567890",
        )

        api_url = build_comments_api_url(context, offset=20, limit=50)

        self.assertIn("action=getcomment", api_url)
        self.assertIn("__biz=MzA3MDAwMDAwMA%3D%3D", api_url)
        self.assertIn("appmsgid=2650000009", api_url)
        self.assertIn("idx=2", api_url)
        self.assertIn("comment_id=1234567890", api_url)
        self.assertIn("offset=20", api_url)
        self.assertIn("limit=50", api_url)

    def test_normalize_comment_payload_supports_script_wrapped_response(self):
        wrapped_payload = """
        window.cgiData = {
          "elected_comment_total_cnt": 1,
          "comment_total_cnt": 2,
          "elected_comment": [
            {
              "nick_name": "精选用户",
              "content": "精选留言",
              "create_time": 1710000000,
              "like_num": 12
            }
          ],
          "comment": [
            {
              "nick_name": "普通用户",
              "content": "普通留言",
              "create_time": 1710001000,
              "reply": {
                "content": "作者回复"
              }
            }
          ]
        };
        """

        normalized = normalize_comment_payload(wrapped_payload)

        self.assertEqual(normalized["selected_count"], 1)
        self.assertEqual(normalized["total_count"], 2)
        self.assertEqual(len(normalized["selected"]), 1)
        self.assertEqual(len(normalized["regular"]), 1)
        self.assertEqual(normalized["regular"][0]["reply_content"], "作者回复")

    def test_renderers_include_comment_sections_and_escape_html(self):
        normalized = {
            "selected_count": 1,
            "total_count": 2,
            "selected": [
                {
                    "author": "Alice<script>",
                    "content": "第一条",
                    "created_at": "2024-03-09 16:00:00",
                    "likes": 3,
                    "reply_content": None,
                }
            ],
            "regular": [
                {
                    "author": "Bob",
                    "content": "第二条",
                    "created_at": "2024-03-09 16:16:40",
                    "likes": 0,
                    "reply_content": "收到",
                }
            ],
        }

        rendered_text = render_comments_text(normalized)
        rendered_html = render_comments_html(normalized)

        self.assertIn("【评论区】", rendered_text)
        self.assertIn("精选留言（1条）", rendered_text)
        self.assertIn("普通留言（1条）", rendered_text)
        self.assertIn("作者回复：收到", rendered_text)
        self.assertIn("&lt;script&gt;", rendered_html)
        self.assertNotIn("Alice<script>", rendered_html)

    def test_redact_url_for_log_removes_query_string(self):
        redacted = redact_url_for_log(
            "https://mp.weixin.qq.com/s?__biz=abc&mid=123&idx=1&sn=secret"
        )

        self.assertEqual(redacted, "https://mp.weixin.qq.com/s")


class FakePage:
    def __init__(self, payload):
        self.payload = payload
        self.last_script = None
        self.last_arg = None

    async def evaluate(self, script, arg):
        self.last_script = script
        self.last_arg = arg
        return self.payload


class WeChatCommentsAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_comments_for_page_uses_comment_api_and_normalizes_result(self):
        article_url = (
            "https://mp.weixin.qq.com/s?"
            "__biz=MzA3MDAwMDAwMA==&mid=2650000001&idx=1&sn=abc"
        )
        page_html = """
        <script>
            var comment_id = "1234567890";
            var appmsgid = "2650000001";
        </script>
        """
        page = FakePage(
            {
                "elected_comment_total_cnt": 1,
                "comment_total_cnt": 1,
                "elected_comment": [
                    {
                        "nick_name": "精选用户",
                        "content": "精选留言",
                        "create_time": 1710000000,
                    }
                ],
                "comment": [],
            }
        )

        normalized = await fetch_comments_for_page(
            page,
            article_url=article_url,
            page_html=page_html,
            limit=30,
        )

        self.assertEqual(normalized["selected_count"], 1)
        self.assertEqual(len(normalized["selected"]), 1)
        self.assertIn("appmsg_comment", page.last_arg["api_url"])
        self.assertIn("limit=30", page.last_arg["api_url"])
        self.assertEqual(page.last_arg["referer"], article_url)


if __name__ == "__main__":
    unittest.main()
