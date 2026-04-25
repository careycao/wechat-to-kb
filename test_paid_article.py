#!/usr/bin/env python3
"""
测试付费文章能否通过 Playwright + wechat_state.json 完整下载。

用法：
    python test_paid_article.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from wechat_collector.wechat_session import (
    DEFAULT_DESKTOP_USER_AGENT,
    build_browser_launch_kwargs,
    ensure_wechat_state_migrated,
)

TEST_URL = "https://mp.weixin.qq.com/s/C7KV5yw2K9R6IdFalyWtDQ"


def strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    return re.sub(r"\s+", " ", text).strip()


async def test() -> None:
    state_path = ensure_wechat_state_migrated()
    if not state_path.exists():
        print("❌ wechat_state.json 不存在，请先运行 ./run.sh --login 登录")
        return

    from playwright.async_api import async_playwright

    print(f"🔍 测试 URL：{TEST_URL}")
    print(f"📂 Session：{state_path}")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(**build_browser_launch_kwargs(headless=True))
        context = await browser.new_context(
            user_agent=DEFAULT_DESKTOP_USER_AGENT,
            storage_state=str(state_path),
        )
        page = await context.new_page()

        print("⏳ 加载页面中...")
        await page.goto(TEST_URL, timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=30000)

        # 额外等待：付费解锁可能需要额外 API 回调
        print("⏳ 等待 JS 执行完毕（3s）...")
        await page.wait_for_timeout(3000)

        # ── 基本信息 ──────────────────────────────────────────────
        title = await page.evaluate(
            "() => document.querySelector('meta[property=\"og:title\"]')?.content || ''"
        )

        # pay_type / is_paid 可能在闭包作用域，从 DOM 文本里正则匹配兜底
        pay_type = await page.evaluate("""() => {
            try { if (typeof pay_type !== 'undefined') return String(pay_type); } catch(e) {}
            const m = document.documentElement.innerHTML.match(/pay_type[\\s:='\"]+([01])/);
            return m ? m[1] : 'N/A';
        }""")
        is_paid = await page.evaluate("""() => {
            try { if (typeof is_paid !== 'undefined') return String(is_paid); } catch(e) {}
            const m = document.documentElement.innerHTML.match(/[^_a-z]is_paid[\\s:='\"]+([01])/);
            return m ? m[1] : 'N/A';
        }""")

        print(f"📰 标题：{title}")
        print(f"💰 pay_type（1=付费文章）：{pay_type}")
        print(f"🔓 is_paid（1=已购买解锁）：{is_paid}")
        print()

        # ── 付费遮罩检测 ──────────────────────────────────────────
        has_filter = await page.evaluate(
            "() => !!document.querySelector('.js_pay_preview_filter')"
        )
        print(f"🚧 付费截断遮罩 js_pay_preview_filter：{'存在（内容被截断）' if has_filter else '不存在（全文可见）'}")
        print()

        # ── 正文提取 ──────────────────────────────────────────────
        js_content = await page.query_selector("#js_content")
        word_count = 0
        if js_content:
            html = await js_content.inner_html()
            text = strip_tags(html)
            word_count = len(text.replace(" ", ""))

            print(f"📄 #js_content 文字量：{word_count} 字")
            print(f"   前 200 字预览：")
            print(f"   {text[:200]}")
            print()
            print(f"   后 200 字预览（判断是否有全文结尾）：")
            print(f"   {text[-200:]}")
        else:
            print("❌ 未找到 #js_content")

        # ── 结论（以 has_filter 和字数为主要依据）────────────────
        print()
        print("=" * 50)
        is_paid_article = pay_type == "1"
        content_complete = not has_filter and word_count > 2000

        if content_complete:
            if is_paid_article:
                print("✅ 结论：付费文章，session 账号已购买，内容完整解锁")
            else:
                print("✅ 结论：普通公开文章，内容正常")
        elif is_paid_article:
            if is_paid == "1":
                print("⚠️  结论：付费文章，is_paid=1 但遮罩仍在，JS 解锁可能需更长等待")
                print("   → 可尝试增加等待时间后重测")
            else:
                print("❌ 结论：付费文章，当前 session 账号未购买，内容截断")
                print(f"   → 当前字数 {word_count}，预计全文 4938 字")
                print("   → 请确认手机微信账号已购买，然后重新运行 ./run.sh --login")
        else:
            print("⚠️  结论：检测到截断遮罩（pay_type 读取失败），内容可能不完整")
            print(f"   → 当前字数 {word_count}，请手动在浏览器确认是否是付费文章")

        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(test())
