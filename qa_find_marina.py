"""QA: найти письмо Марины (личная почта, дала номер для аудита) во всех ящиках.
READ-ONLY. Ищем по ключевым словам в теле/теме. Без записи в БД.
"""
import sys
import imaplib
import email as email_lib
from email.utils import parseaddr

# принудительно UTF-8 вывод (Windows cp1251 падает на эмодзи)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import agent_recorder as R
from agent_recorder import (gc, get_accounts, _decode_header, _domain_of,
                            _safe_select, list_mailboxes, _extract_preview)

KEYWORDS = ("марина", "аудит", "скин", "номер", "аудитэ", "аудиты", "кинить",
            "кидать", "пришл", "скину", "телефон", "whatsapp", "telegram", "тг")
LOOKBACK = 120


def _search_in_box(m, box, email_addr):
    hits = []
    try:
        if not _safe_select(m, box):
            return hits
    except Exception:
        return hits
    since = (R.datetime.now() - R.timedelta(days=LOOKBACK)).strftime("%d-%b-%Y")
    try:
        typ, data = m.search(None, "SINCE", since)
    except Exception:
        return hits
    if typ != "OK" or not data or not data[0]:
        return hits
    for num in data[0].split():
        try:
            typ, msg_data = m.fetch(num, "(RFC822)")
        except Exception:
            continue
        if typ != "OK":
            continue
        msg = email_lib.message_from_bytes(msg_data[0][1])
        subject = _decode_header(msg.get("Subject", ""))
        from_email = parseaddr(msg.get("From", ""))[1].strip().lower()
        fdom = _domain_of(from_email)
        # пропускаем свои же ящики
        if from_email in {a[0].lower() for a in get_accounts(gc.load_env())}:
            continue
        body = _extract_preview(msg) or ""
        blob = (subject + " " + body).lower()
        if any(k in blob for k in KEYWORDS):
            hits.append((fdom, from_email, subject.strip(), body.strip()[:300]))
    return hits


def main():
    env = gc.load_env()
    accounts = get_accounts(env)
    print(f"=== поиск Марины/аудита по {len(accounts)} ящикам за {LOOKBACK} дней ===")
    total = 0
    for email_addr, password in accounts:
        try:
            m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            m.login(email_addr, password)
        except Exception as e:
            print(f"  [login fail {email_addr[:4]}***: {e}]")
            continue
        boxes = []
        try:
            bx = list_mailboxes(m)
            boxes = [i["orig"] for i in bx.values()]
        except Exception:
            pass
        if not boxes:
            boxes = ["INBOX"]
        for box in dict.fromkeys(boxes):
            try:
                hits = _search_in_box(m, box, email_addr)
            except Exception:
                continue
            for fdom, femail, subj, body in hits:
                total += 1
                print(f"\n  ЯЩИК {email_addr[:4]}*** / {box}")
                print(f"  FROM: {femail} (domain={fdom})")
                print(f"  SUBJ: {subj}")
                print(f"  BODY: {body}")
        m.logout()
    print(f"\n=== найдено совпадений: {total} ===")
    if total == 0:
        print("(пусто — значит письмо не в этих 5 ящиках, или старее 120 дней)")


if __name__ == "__main__":
    main()
