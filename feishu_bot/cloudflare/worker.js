/**
 * Cloudflare Worker — 接收飞书机器人消息，写入多维表。
 *
 * 环境变量通过 wrangler secret put 配置：
 *   FEISHU_APP_ID        飞书应用 App ID
 *   FEISHU_APP_SECRET    飞书应用 App Secret
 *   FEISHU_VERIFY_TOKEN  飞书事件订阅验证 Token
 *   BITABLE_APP_TOKEN    多维表 App Token
 *   BITABLE_TABLE_ID     数据表 Table ID
 */

const FEISHU_BASE = "https://open.feishu.cn";
const URL_RE = /https?:\/\/[^\s<>"'{}|\\^`[\]]+/g;

function jsonResp(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function getAccessToken(env) {
  const res = await fetch(
    `${FEISHU_BASE}/open-apis/auth/v3/tenant_access_token/internal`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        app_id: env.FEISHU_APP_ID,
        app_secret: env.FEISHU_APP_SECRET,
      }),
    },
  );
  const data = await res.json();
  return data.tenant_access_token;
}

async function addBitableRecord(url, token, env) {
  await fetch(
    `${FEISHU_BASE}/open-apis/bitable/v1/apps/${env.BITABLE_APP_TOKEN}/tables/${env.BITABLE_TABLE_ID}/records`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        fields: { URL: url, 标题: "", 状态: "待处理", 备注: "" },
      }),
    },
  );
}

async function replyMessage(messageId, text, token) {
  await fetch(`${FEISHU_BASE}/open-apis/im/v1/messages/${messageId}/reply`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      content: JSON.stringify({ text }),
      msg_type: "text",
    }),
  });
}

function extractText(body) {
  if (body.schema === "2.0") {
    const msg = body?.event?.message ?? {};
    const text = JSON.parse(msg.content ?? "{}").text ?? "";
    return { messageId: msg.message_id ?? "", text };
  }
  const event = body.event ?? {};
  const messageId = event.message_id ?? event.msg_id ?? "";
  let text = event.text ?? event.content ?? "";
  if (typeof text === "string" && text.startsWith("{")) {
    text = JSON.parse(text).text ?? text;
  }
  return { messageId, text };
}

async function processUrls(urls, messageId, env) {
  try {
    const token = await getAccessToken(env);
    await Promise.all(urls.map((url) => addBitableRecord(url, token, env)));
    const lines = urls
      .map((u) => `• ${u.length > 60 ? u.slice(0, 60) + "..." : u}`)
      .join("\n");
    if (messageId)
      await replyMessage(
        messageId,
        `✅ 已收录 ${urls.length} 条链接，等待定时下载。\n${lines}`,
        token,
      );
  } catch (err) {
    console.error("写入多维表失败", err);
    try {
      const token = await getAccessToken(env);
      if (messageId)
        await replyMessage(messageId, `❌ 保存失败：${err.message}`, token);
    } catch (_) {}
  }
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST")
      return jsonResp({ error: "method not allowed" }, 405);

    let body;
    try {
      body = await request.json();
    } catch {
      return jsonResp({ error: "invalid json" }, 400);
    }

    // ── URL 验证握手（立即响应，不做任何异步操作）──────────────────────────
    if (body.type === "url_verification" || (body.challenge && !body.event)) {
      return jsonResp({ challenge: body.challenge ?? "" });
    }

    // ── Token 校验 ────────────────────────────────────────────────────────
    const receivedToken = body.token ?? body.header?.token ?? "";
    if (env.FEISHU_VERIFY_TOKEN && receivedToken !== env.FEISHU_VERIFY_TOKEN) {
      return jsonResp({ error: "token mismatch" }, 403);
    }

    // ── 解析消息 ──────────────────────────────────────────────────────────
    const { messageId, text } = extractText(body);
    if (!text) return jsonResp({ msg: "ignored" });

    const urls = text.match(URL_RE) ?? [];
    if (urls.length === 0) return jsonResp({ msg: "no url found" });

    // ── 先立即返回 200，异步写入多维表（飞书不等处理结果）────────────────
    ctx.waitUntil(processUrls(urls, messageId, env));
    return jsonResp({ msg: "ok" });
  },
};
