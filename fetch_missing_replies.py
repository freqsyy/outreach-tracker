"""Точечный забор полных текстов ответов для конкретных сайтов.

Не сканирует всю почту (как agent_recorder), а ищет входящие ТОЛЬКО по
домену отправителя для заданных сайтов и дописывает полный текст через
_extend_reply_if_truncated (заменяет старый обрезок, дублей не создаёт).

USAGE: python fetch_missing_replies.py
"""
import imaplib
import email as email_lib
from email.utils import parseaddr

import agent_recorder as R
from agent_recorder import (gc, get_accounts, _decode_header, _domain_of,
                            _safe_select, _extract_preview,
                            _extend_reply_if_truncated, list_mailboxes)

# (id сайта, домен сайта для матча по отправителю)
TARGETS = [
    (61, "chudorukami.ru"),
    (202, "vean-tattoo.com"),
]


def _scan_for(site_id, site_domain, email_addr, password, lookback_days):
    """Ищет письма от site_domain во входящих аккаунта и дописывает полный текст."""
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        m.login(email_addr, password)
    except Exception as e:
        gc.log(f"login fail {email_addr}: {e}", "FETCH_MISSING")
        return
    # собираем ВСЕ папки (не только INBOX/Junk/All)
    scan = []
    try:
        boxes = list_mailboxes(m)  # decoded -> {orig, flags}
        scan = [info["orig"] for info in boxes.values()]
    except Exception:
        pass
    if not scan:
        scan = ["INBOX"]
    since = (R.datetime.now() - R.timedelta(days=lookback_days)).strftime("%d-%b-%Y")
    found = False

    def _soft_match(frm_domain):
        # мягкий матч: точное совпадение ИЛИ домен сайта вложен в домен отправителя
        # (ловит поддомены/формы обратной связи, которые точный == пропускает)
        frm_domain = (frm_domain or "").lower()
        if not frm_domain:
            return False
        return frm_domain == site_domain or frm_domain.endswith("." + site_domain) \
            or site_domain.endswith("." + frm_domain)

    for box in dict.fromkeys(scan):  # уникальные без изменения порядка
        try:
            if not _safe_select(m, box):
                continue
        except Exception:
            continue
        try:
            typ, data = m.search(None, "SINCE", since)
        except Exception:
            continue
        if typ != "OK" or not data or not data[0]:
            continue
        for num in data[0].split():
            try:
                typ, msg_data = m.fetch(num, "(RFC822)")
            except Exception:
                continue
            if typ != "OK":
                continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            from_email = parseaddr(msg.get("From", ""))[1].strip().lower()
            frm_domain = _domain_of(from_email)
            subject = _decode_header(msg.get("Subject", ""))
            # матч либо по домену отправителя, либо по теме (Re: ваш <domain> / Нашёл)
            subj_l = subject.lower()
            topic_hit = (site_domain[:-4] if site_domain.endswith(".ru") else site_domain) in subj_l
            if not (_soft_match(frm_domain) or topic_hit):
                continue
            body = _extract_preview(msg)
            if not body:
                continue
            replaced = _extend_reply_if_truncated(site_id, subject.strip(), body)
            gc.log(f"[{site_domain}] box={box} from={from_email} subj='{subject[:40]}' "
                   f"body_len={len(body)} replaced={replaced}", "FETCH_MISSING")
            found = True
    m.logout()
    if not found:
        gc.log(f"[{site_domain}] pisem ot etogo domena ne naydeno v {email_addr}.",
               "FETCH_MISSING")


def main():
    env = gc.load_env()
    accounts = get_accounts(env)
    lookback = max(int(env.get("IMAP_LOOKBACK_DAYS", "14")), 60)
    if not accounts:
        print("Нет аккаунтов в .env (ACCOUNT_N_EMAIL/PASS)")
        return
    for site_id, domain in TARGETS:
        for email_addr, password in accounts:
            _scan_for(site_id, domain, email_addr, password, lookback)
    print("Готово. Проверь БД.")


if __name__ == "__main__":
    main()
