/**
 * Vercel Serverless Function (Node.js) — 接收飞书机器人消息，写入多维表。
 *
 * 环境变量（Vercel 控制台配置）：
 *   FEISHU_APP_ID        飞书应用 App ID
 *   FEISHU_APP_SECRET    飞书应用 App Secret
 *   FEISHU_VERIFY_TOKEN  飞书事件订阅验证 Token
 *   BITABLE_APP_TOKEN    多维表 App Token
 *   BITABLE_TABLE_ID     数据表 Table ID
 */

const FEISHU_BASE = "https://open.feishu.cn";
const URL_RE = /https?:\/\/[^\s<>"'{}|\\^`[\]]+/g;

async function getAccessToken() {
  const res = await fetch(
    `${FEISHU_BASE}/open-apis/auth/v3/tenant_access_token/internal`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        app_id: process.env.FEISHU_APP_ID,
        app_secret: process.env.FEISHU_APP_SECRET,
      }),
    },
  );
  const data = await res.json();
  return data.tenant_access_token;
}

async function addBitableRecord(url, token) {
  await fetch(
    `${FEISHU_BASE}/open-apis/bitable/v1/apps/${process.env.BITABLE_APP_TOKEN}/tables/${process.env.BITABLE_TABLE_ID}/records`,
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
  // v2.0 schema
  if (body.schema === "2.0") {
    const msg = body?.event?.message ?? {};
    const messageId = msg.message_id ?? "";
    const content = msg.content ?? "{}";
    const text = JSON.parse(content).text ?? "";
    return { messageId, text };
  }
  // v1.0 schema
  const event = body.event ?? {};
  const messageId = event.message_id ?? event.msg_id ?? "";
  let text = event.text ?? event.content ?? "";
  if (typeof text === "string" && text.startsWith("{")) {
    text = JSON.parse(text).text ?? text;
  }
  return { messageId, text };
}

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "method not allowed" });
  }

  const body = req.body;

  // ── URL 验证握手 ──────────────────────────────────────────────────────────
  if (body.type === "url_verification" || (body.challenge && !body.event)) {
    return res.status(200).json({ challenge: body.challenge ?? "" });
  }

  // ── Token 校验 ────────────────────────────────────────────────────────────
  const verifyToken = process.env.FEISHU_VERIFY_TOKEN;
  const receivedToken = body.token ?? body.header?.token ?? "";
  if (verifyToken && receivedToken !== verifyToken) {
    return res.status(403).json({ error: "token mismatch" });
  }

  // ── 解析消息 ──────────────────────────────────────────────────────────────
  const { messageId, text } = extractText(body);
  if (!text) return res.status(200).json({ msg: "ignored" });

  const urls = text.match(URL_RE) ?? [];
  if (urls.length === 0) return res.status(200).json({ msg: "no url found" });

  // ── 写入多维表 ────────────────────────────────────────────────────────────
  try {
    const token = await getAccessToken();
    await Promise.all(urls.map((url) => addBitableRecord(url, token)));

    const lines = urls
      .map((u) => `• ${u.length > 60 ? u.slice(0, 60) + "..." : u}`)
      .join("\n");
    const replyText = `✅ 已收录 ${urls.length} 条链接，等待定时下载。\n${lines}`;
    if (messageId) await replyMessage(messageId, replyText, token);
  } catch (err) {
    console.error("写入多维表失败", err);
    try {
      const token = await getAccessToken();
      if (messageId)
        await replyMessage(messageId, `❌ 保存失败：${err.message}`, token);
    } catch (_) {}
  }

  return res.status(200).json({ msg: "ok" });
}
