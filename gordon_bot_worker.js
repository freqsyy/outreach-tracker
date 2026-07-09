// gordon_bot_worker.js - Telegram-бот Гордона на Cloudflare Worker.
// Бесплатно, без карты, всегда онлайн. Читает status.json из GitHub, /ask гонит в OpenRouter free.
//
// Secrets (Settings -> Variables -> Secrets):
//   TG_BOT_TOKEN     - токен от @BotFather
//   ALLOWED_CHAT     - твой chat_id (чужие игнорятся)
//   OPENROUTER_API_KEY - бесплатный ключ OpenRouter (для /ask)
//   STATUS_URL       - https://raw.githubusercontent.com/<user>/<repo>/<branch>/status.json
//
// Deploy: вставить код в Cloudflare Worker, задать секреты, задеплоить.
// Потом один раз: открыть <worker-url>/setwebhook в браузере (пропишет webhook).

const TELEGRAM_API = "https://api.telegram.org/bot";
const OR_MODEL = "deepseek/deepseek-chat-v3-0324:free"; // бесплатная модель

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // --- один раз прописать webhook ---
    if (url.pathname === "/setwebhook" && request.method === "GET") {
      const wh = await fetch(
        `${TELEGRAM_API}${env.TG_BOT_TOKEN}/setWebhook?url=${encodeURIComponent(url.origin + "/")}`
      ).then((r) => r.json());
      return json(wh);
    }

    if (url.pathname === "/") {
      // health check
      if (request.method === "GET") return json({ ok: true, bot: "gordon" });
      if (request.method === "POST") {
        let update;
        try {
          update = await request.json();
        } catch {
          return json({ ok: false });
        }
        const msg = update.message || update.edited_message;
        if (msg && msg.text) {
          const chatId = String(msg.chat.id);
          if (env.ALLOWED_CHAT && chatId !== String(env.ALLOWED_CHAT)) {
            return json({ ok: true, ignored: chatId });
          }
          await handle(chatId, msg.text, env);
        }
        return json({ ok: true });
      }
    }
    return json({ ok: false, error: "not found" }, 404);
  },
};

async function handle(chatId, text, env) {
  const t = (text || "").trim();
  const low = t.toLowerCase();

  if (low === "/start" || low === "/help" || low === "help") {
    return send(chatId, env,
      "GordonBot.\n/status - сводка Гордона + хвост лога\n" +
      "/log [N] - последние N строк лога\n" +
      "/ask <вопрос> - спросить модель (OpenRouter, бесплатно)\n" +
      "(любой другой текст = /status)");
  }

  if (low.startsWith("/log")) {
    const n = parseInt((t.split(" ")[1] || "").trim(), 10) || 15;
    const st = await getStatus(env);
    if (!st) return send(chatId, env, "status.json недоступен.");
    const tail = (st.log_tail || "").split("\n").slice(-n).join("\n");
    return send(chatId, env, `=== gordon_run.log (последние ${n}) ===\n${tail}`);
  }

  if (low.startsWith("/ask")) {
    const q = t.slice(4).trim();
    if (!q) return send(chatId, env, "После /ask напиши вопрос.");
    if (!env.OPENROUTER_API_KEY)
      return send(chatId, env, "OPENROUTER_API_KEY не задан в секретах воркера.");
    const ans = await askOpenRouter(q, env);
    return send(chatId, env, "Ответ:\n" + ans);
  }

  // всё остальное -> статус
  const st = await getStatus(env);
  if (!st) return send(chatId, env, "status.json недоступен.");
  const body =
    `Обновлено: ${st.generated_at}\n\n` +
    `${st.stats || "(нет stats)"}\n\n` +
    `=== лог (хвост) ===\n${(st.log_tail || "").split("\n").slice(-10).join("\n")}`;
  return send(chatId, env, body);
}

async function getStatus(env) {
  if (!env.STATUS_URL) return null;
  try {
    const r = await fetch(env.STATUS_URL, { cf: { cacheTtl: 30 } });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

async function askOpenRouter(q, env) {
  try {
    const r = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.OPENROUTER_API_KEY}`,
        "Content-Type": "application/json",
        "HTTP-Referer": "https://gordon.local",
        "X-Title": "GordonBot",
      },
      body: JSON.stringify({
        model: OR_MODEL,
        messages: [{ role: "user", content: q }],
        max_tokens: 800,
      }),
    });
    const j = await r.json();
    return j?.choices?.[0]?.message?.content?.trim() || "(пустой ответ)";
  } catch (e) {
    return "Ошибка OpenRouter: " + e.message;
  }
}

async function send(chatId, env, text) {
  // Telegram лимит 4096 знаков - разбиваем
  const chunks = chunk(text, 4096);
  for (const c of chunks) {
    await fetch(`${TELEGRAM_API}${env.TG_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text: c }),
    });
  }
}

function chunk(text, size) {
  if (text.length <= size) return [text];
  const out = [];
  let cur = "";
  for (const line of text.split("\n")) {
    if (cur.length + line.length + 1 > size) {
      if (cur) out.push(cur);
      cur = line;
      while (cur.length > size) {
        out.push(cur.slice(0, size));
        cur = cur.slice(size);
      }
    } else {
      cur = cur ? cur + "\n" + line : line;
    }
  }
  if (cur) out.push(cur);
  return out;
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
