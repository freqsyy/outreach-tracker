"""QA: видит ли рекордер ВСЕХ ответивших? (READ-ONLY, без записи в БД)

Лезет во все ящики по IMAP, вытягивает входящие с темой Re:/Ответ/ваш <domain>
за последние N дней, матчит по домену сайта из БД и сверяет со статусом replied.

Пошаговый print с flush - виден прогресс, не висит в тишине.
USAGE: python qa_check_all_replies.py
"""
import sys
import imaplib
import email as email_lib
from email.utils import parseaddr

import agent_recorder as R
from agent_recorder import (gc, get_accounts, _decode_header, _domain_of,
                            _safe_select, list_mailboxes)

LOOKBACK = 30
MAX_PER_BOX = 400


def log(s):
    print(s, flush=True)


def _collect_replies(email_addr, password):
    out = []
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        m.login(email_addr, password)
    except Exception as e:
        log(f"  [login fail {email_addr[:4]}***: {e}]")
        return out
    boxes = []
    try:
        bx = list_mailboxes(m)
        boxes = [i["orig"] for i in bx.values()]
    except Exception:
        pass
    if not boxes:
        boxes = ["INBOX"]
    since = (R.datetime.now() - R.timedelta(days=LOOKBACK)).strftime("%d-%b-%Y")
    for box in dict.fromkeys(boxes):
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
        nums = data[0].split()[:MAX_PER_BOX]
        for num in nums:
            try:
                typ, msg_data = m.fetch(num, "(RFC822)")
            except Exception:
                continue
            if typ != "OK":
                continue
            msg = email_lib.message_from_bytes(msg_data[0][1])
            subject = _decode_header(msg.get("Subject", ""))
            sl = subject.lower()
            if not (sl.startswith("re:") or "ответ" in sl or "ваш " in sl
                    or "нашёл" in sl or "нашел" in sl):
                continue
            from_email = parseaddr(msg.get("From", ""))[1].strip().lower()
            out.append((_domain_of(from_email), from_email, subject.strip()))
    m.logout()
    return out


def main():
    env = gc.load_env()
    accounts = get_accounts(env)
    if not accounts:
        log("Нет аккаунтов в .env")
        return

    conn = gc.get_conn()
    sites = conn.execute("SELECT id, url, email, status FROM sites").fetchall()
    conn.close()
    site_domains = {}
    for s in sites:
        u = (s["url"] or "").lower()
        d = u.split("//")[-1].split("/")[0].replace("www.", "")
        site_domains[d] = s

    log(f"=== QA: опрос {len(accounts)} ящиков за {LOOKBACK} дней ===")
    all_replies = []
    for email_addr, password in accounts:
        log(f"[*] опрос {email_addr[:4]}*** ...")
        reps = _collect_replies(email_addr, password)
        log(f"    найдено Re:-писем: {len(reps)}")
        all_replies.extend(reps)

    matched = {}
    for fdom, femail, subj in all_replies:
        for sd, s in site_domains.items():
            if fdom == sd or fdom.endswith("." + sd) or sd.endswith("." + fdom):
                matched[s["id"]] = (sd, femail, subj)
                break

    log(f"\n=== ИТОГО матчнутых к сайтам: {len(matched)} ===")
    for sid in sorted(matched):
        sd, femail, subj = matched[sid]
        st = site_domains[sd]["status"]
        ok = "OK(replied)" if st == "replied" else "*** ПРОПУЩЕНО ***"
        log(f"  #{sid} {site_domains[sd]['url']}  status={st}  {ok}")
        log(f"      from={femail}  subj='{subj[:70]}'")

    missed = [sid for sid in matched if site_domains[matched[sid][0]]["status"] != "replied"]
    log(f"\n=== ДЫРА (ответили, но НЕ в replied): {len(missed)} ===")
    for sid in missed:
        sd, femail, subj = matched[sid]
        log(f"  #{sid} {site_domains[sd]['url']} from={femail} :: {subj[:70]}")
    if not missed:
        log("  (пусто - все ответы в replied)")


if __name__ == "__main__":
    main()
