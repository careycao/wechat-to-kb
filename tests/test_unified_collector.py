import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from unified_collector import (  # noqa: E402
    _resolve_auto_route,
    decide_fetch_mode,
    is_ambiguous_note_host,
    should_use_video_collector,
    video_plain_too_thin,
)


class UnifiedCollectorRoutingTest(unittest.TestCase):
    def test_decide_fetch_mode_prioritizes_wechat_articles(self):
        self.assertEqual(
            decide_fetch_mode("https://mp.weixin.qq.com/s/abc"),
            "wechat",
        )

    def test_decide_fetch_mode_routes_video_hosts_to_video_pipeline(self):
        self.assertEqual(
            decide_fetch_mode("https://www.bilibili.com/video/BV1xx411c7mD"),
            "video",
        )
        self.assertEqual(
            decide_fetch_mode("https://www.xiaohongshu.com/explore/123"),
            "video",
        )

    def test_decide_fetch_mode_routes_other_pages_to_web(self):
        self.assertEqual(
            decide_fetch_mode("https://example.com/article"),
            "web",
        )

    def test_video_plain_too_thin_detects_short_body_after_separator(self):
        self.assertTrue(video_plain_too_thin({"plain_text": "标题\n\n---\n\n过短正文"}))
        self.assertFalse(
            video_plain_too_thin(
                {
                    "plain_text": "标题\n\n---\n\n"
                    + "这是一个足够长的正文。".join(["内容"] * 20)
                }
            )
        )

    def test_xiaohongshu_hosts_are_marked_ambiguous(self):
        self.assertTrue(is_ambiguous_note_host("https://www.xiaohongshu.com/explore/123"))
        self.assertTrue(is_ambiguous_note_host("https://xhslink.com/abcdef"))
        self.assertFalse(is_ambiguous_note_host("https://example.com/post"))

    def test_video_host_detection_covers_major_platforms(self):
        self.assertTrue(should_use_video_collector("https://youtu.be/abc"))
        self.assertTrue(should_use_video_collector("https://channels.weixin.qq.com/web/pages/feed"))
        self.assertFalse(should_use_video_collector("https://mp.weixin.qq.com/s/abc"))

    def test_video_host_detection_ignores_query_string_mentions(self):
        self.assertFalse(
            should_use_video_collector("https://example.com/post?ref=https://youtube.com/watch?v=abc")
        )


class UnifiedCollectorAutoRouteTest(unittest.TestCase):
    def test_non_interactive_flag_forces_auto_route(self):
        args = type("Args", (), {"interactive": False, "non_interactive": True})()
        self.assertTrue(_resolve_auto_route(args))

    def test_interactive_flag_overrides_non_tty_default(self):
        args = type("Args", (), {"interactive": True, "non_interactive": False})()
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            self.assertFalse(_resolve_auto_route(args))


if __name__ == "__main__":
    unittest.main()
