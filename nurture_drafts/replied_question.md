# SAMPLE CHAIN 2 - state: replied_question  (4 ветки возражений, seq="v3")
# Движок: agent_nurture.py выбирает ОДНУ ветку по classify_objection(reply).
# Плейсхолдеры: {site} {tg} {bug} {case} {answer}
# {answer} = текст вопроса лида из notes (CLIENT::replied::), если есть.
# DRY-RUN: НЕ отправляется. Только превью.

# ---------- Ветка A (timing) - когда вернуться ----------
Subject: re: timing on {site}

Hi {site} team,

Totally fair - timing's real, not an objection. I'm not going anywhere.

When would actually be convenient to pick this back up? Next month, after a
launch, Q4? You name it - I'll ping you then, zero pressure, no calendar guilt.

Meanwhile the free 3-bug audit of {site} can run in the background now and sit
ready whenever you are.

CTA: reply with a timeframe, or {tg}. I'll set the reminder.

- a QA worker

# ---------- Ветка B (portfolio) - один реальный кейс ----------
Subject: re: a case for {site}

Hi {site} team,

Makes sense you'd want proof, not promises. Here's one that hits close to home.

On {site} I found {bug}. Almost the same shape as {case}: one indie store lost
~12% of signups to a single mislabeled input - quiet, daily, invisible in analytics.
The audit caught it, they fixed it, recovered the signups.

That's the whole portfolio in one story: real bug, real money, fixed.

CTA: want the full audit of {site}? reply "AUDIT" or {tg}.

- a QA worker

# ---------- Ветка C (undecided) - 5 точек самопроверки ----------
Subject: re: a 5-point check for {site}

Hi {site} team,

No rush on the decision - here's something useful either way. Five spots where
{site} most often leaks users, check them yourself:

1. Signup field that accepts input but never validates it
2. Checkout button that works on desktop, dead on mobile
3. Form that "succeeds" but sends no confirmation
4. Error message that blames the user for a server bug
5. A flow you've never clicked end-to-end as a stranger

If even one bites, the free 3-bug audit maps the rest. No buy needed.

CTA: reply which one you found, or {tg}.

- a QA worker

# ---------- Ветка D (has_qa) - прямой ответ + 1 CTA ----------
Subject: re: your QA team on {site}

Hi {site} team,

Good - having QA is a real advantage, not a reason to say no. {answer}

I'm not here to replace your team. I'm the second pair of eyes from outside:
fresh view catches what internal folks skip (especially real devices + edge
cases). One indie store found 3 bugs their in-house QA had walked past for months.

The free 3-bug audit of {site} shows the difference on facts - your call after.

CTA: reply "AUDIT" or ping {tg}. No pitch beyond this.

- a QA worker
