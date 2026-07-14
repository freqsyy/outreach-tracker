# SAMPLE CHAIN 3 - state: reactivate_30d  (1 финальный тач, seq="react" / touch 1)
# Движок: agent_nurture.py, seq="react", touch 1. Плейсхолдеры: {site} {tg} {bug}
# Логика: лид молчал >=30 дней. Лёгкий, без давления, напоминает ценность (баг),
# мягкий CTA «напиши когда будешь готов». Touch 2/3 (LR + breakup) берутся из
# playbook_7 сценарий 5 при необходимости.
# DRY-RUN: НЕ отправляется. Только превью.

# ---------- Touch 1 (day 0 of reactivation) - REACTIVATE / FRESH-LOOK ----------
Subject: re: still on the table for {site}?

Hi {site} team,

It's been about a month - just checking in, no pressure at all.

I clicked through {site} a while back and {bug} is still the one that stuck with
me: small on the surface, but exactly the thing that quietly costs first-time
signups. Worth a free 24h re-look whenever the timing's right.

If now's not the moment, totally fair - just say the word and I'll leave you be,
or ping {tg} when you're ready. The offer doesn't expire.

- a QA worker
