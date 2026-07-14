# SAMPLE CHAIN 1 - state: opened_no_reply  (3-touch, +3 / +7 / +14)
# Движок: agent_nurture.py, seq="v2". Плейсхолдеры: {site} {tg} {bug} {checklist_link}
# Логика: лид открыл письмо, но не ответил. Мягко спрашиваем -> proof -> soft-final.
# DRY-RUN: НЕ отправляется. Только превью.

# ---------- Touch 1 (+3) - LC / LAUNCH READINESS (короткий вопрос) ----------
Subject: re: что остановило на {site}?

Hi {site} team,

You opened my last note - I noticed, and that's enough to tell me the timing
isn't dead. No pitch here.

Genuine question: what stopped you? Too soon, not a fit, or just buried in
other fires? One word back is fine - it helps me know if I'm useful or noise.

If it's the second, I can still run the free 3-bug audit of {site} in 24h,
you keep the report.

CTA: reply with one word, or ping {tg}. Door open either way.

- a QA worker

# ---------- Touch 2 (+7) - LR / LOST REVENUE (proof-снипет) ----------
Subject: re: what {site} might be leaking

Hi {site} team,

Following up with something concrete, not a nudge.

On {site} I found {bug} - and that's not cosmetic. A broken field or dead
button at the signup step doesn't shout, it just quietly drops people from
your funnel. I watched one indie store lose ~12% of signups to a single
mislabeled input. At your volume that's real money walking out.

The free 3-bug audit catches it on paper, you keep the map either way.

CTA: send your URL, get the leak map. Or {tg}.

- a QA worker

# ---------- Touch 3 (+14) - SOFT FINAL (чек-лист, без давления) ----------
Subject: re: last one, {site} - a gift

Hi {site} team,

Last one from me, promise. No pressure at all - if now's not the moment, fair.

Since you're clearly hands-on with {site}, here's a free 10-point launch QA
checklist I use: {checklist_link}. Run it yourself, no strings, no reply needed.

If the timing ever shifts, the free 3-bug audit is one message away at {tg}.
Wishing {site} smooth launches.

- a QA worker
