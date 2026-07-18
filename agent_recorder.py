#!/usr/bin/env python3
"""
agent_recorder.py — АГЕНТ 3 (Летописец).

Фиксирует результаты откликов:
1. РУЧНОЙ режим (по умолчанию): читает gordon_responses.txt,
   где каждая строка: <id> <replied|hired|rejected> [сумма BYN]
   и обновляет статус в track.py.
2. IMAP-поллинг (ВКЛ если IMAP_ENABLED=true в .env): ищет ВСЕ ответы
   владельцев сайтов и помечает replied.

=== КАК ЛОВИМ ОТВЕТЫ (цель: видеть ВООБЩЕ все ответы) ===
Проблема старой версии: матч ТОЛЬКО если from_email ответа == email, КОМУ
писали. Владелец отвечает с личного ящика (marina@gmail.com) -> терялось.

Решение — четыре слоя матча, плюс сохранение всего, что не матчнулось:
  ЭТАП 1 — строим мапу Message-ID нашего письма -> site_id из папки Sent
           КАЖДОГО аккаунта (To: письма -> email_map -> site_id).
  ЭТАП 2 — сканируем ВСЕ папки (Inbox/Спам/Вся почта/forwarded) каждого
           аккаунта на входящие:
     (а) цепочка: In-Reply-To / References содержит наш Message-ID -> site_id
         (независимо от того, с какого ящика владелец ответил)
     (б) мягкий домен: домен From ответа == домен сайта (или вложен)
     (в) topic-hit: название сайта / домен без tld встречается в теме ИЛИ теле
     (г) Delivered-To: на какой из наших акков доставлено -> ищем его Sent-цепочку
  ЭТАП 3 — всё, что НЕ матчнулось к сайту (минус служебный мусор), сохраняем
           в таблицу inbound_unmatched. Ничего не теряется — разберём руками
           или дообучим матч. Дедуп по (from_email, subject, preview).

Запуск:  python agent_recorder.py [--dry-run]
  --dry-run : сканирует почту и логирует находки, НЕ пишет в БД и НЕ меняет
              статусы. Используем для проверки перед боевым прогоном.
"""

import os
import re
import imaplib
import email as email_lib
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from email.utils import parseaddr, getaddresses

import gordon_common as gc

# Надёжное извлечение email-адресов из любого заголовка (To/From/Delivered-To/...).
# email.utils.getaddresses ломается на адресах без угловых скобок <...> (съедает
# первую букву: "info@x.by" -> "nfo@x.by"), поэтому дублируем regex-извлечением.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")

def extract_emails(header_value):
    """Возвращает список email-адресов (lower) из значения заголовка.
    Использует regex поверх getaddresses: так покрываем и 'addr@x.by' (без
    скобок), и '<addr@x.by>', и списки через запятую."""
    if not header_value:
        return []
    found = set()
    if isinstance(header_value, list):
        header_value = ", ".join(header_value)
    found.update(a[1].strip().lower() for _, a in getaddresses([header_value]) if a[1].strip())
    found.update(x.lower() for x in _EMAIL_RE.findall(header_value))
    return [e for e in found if "@" in e]

HERE = os.path.dirname(os.path.abspath(__file__))
RESPONSES = os.path.join(HERE, "gordon_responses.txt")
TRACK = os.path.join(HERE, "track.py")

# Названия папок на разных локалях Gmail (реальные имена подтягиваются через
# list_mailboxes(), это запасной сет).
_SENT_CANDIDATES = ["[Gmail]/Sent Mail", "[Gmail]/Отправленные", "[Gmail]/Sent",
                    "Sent Mail", "Sent", "Отправлено"]
_SPAM_CANDIDATES = ["[Gmail]/Spam", "[Gmail]/Спам", "Spam", "Спам", "Junk", "Bulk Mail"]
_ALL_CANDIDATES = ["[Gmail]/All Mail", "[Gmail]/Вся почта", "All Mail", "Вся почта"]

# Статусы, которые считаем "уже писали этому сайту" (кандидаты на ответ)
_SENT_LIKE = ("sent", "replied", "hired", "rejected", "bounced")


def get_accounts(env):
    """Список аккаунтов из .env: ACCOUNT_1_EMAIL/PASS ... (тот же App Password, что и для SMTP)."""
    accs = []
    i = 1
    while True:
        e = env.get(f"ACCOUNT_{i}_EMAIL")
        p = env.get(f"ACCOUNT_{i}_PASS")
        if not e or not p:
            break
        accs.append((e, p))
        i += 1
    return accs


def _decode_header(val):
    """Декодирует MIME-заголовок (=?utf-8?B?...?=) в читаемую строку."""
    if not val:
        return ""
    try:
        from email.header import decode_header, make_header
        return str(make_header(decode_header(val)))
    except Exception:
        return val


def _domain_of(email_addr):
    """Домен из email (lower). Пусто если нет @."""
    e = (email_addr or "").strip().lower()
    if "@" not in e:
        return ""
    return e.rpartition("@")[2]


def _domain_from_url(url):
    if not url:
        return ""
    m = re.match(r"https?://([^/]+)/?", url, re.I)
    return (m.group(1) if m else url).lower().strip()


def list_mailboxes(m):
    """Возвращает dict: decoded_name -> {orig, flags}.
    Параллельно тащим флаги из сырого list() (\\Sent / \\Junk / \\All),
    чтобы выбирать папки НЕЗАВИСИМО от локали имени."""
    result = {}
    try:
        typ, data = m.list()
        if typ != "OK":
            return result
        for raw in data:
            if not raw:
                continue
            s = raw.decode("utf-8", "ignore")
            mm = re.findall(r'"((?:[^"\\]|\\.)*)"', s)
            orig = mm[-1] if mm else s.split()[-1].strip()
            if not orig:
                continue
            flags = set(re.findall(r"\\([A-Za-z]+)", s))
            result[_decode_imap_utf7(orig)] = {"orig": orig, "flags": flags}
    except Exception:
        pass
    return result


def _decode_imap_utf7(name):
    """Декодирует IMAP-UTF7 (modified base64 между & и -) в читаемую строку."""
    if "&" not in name:
        return name
    try:
        import base64
        out = []
        i = 0
        while i < len(name):
            if name[i] == "&" and (i + 1 >= len(name) or name[i + 1] != "-"):
                j = name.find("-", i + 1)
                if j == -1:
                    out.append(name[i:]); break
                chunk = name[i + 1:j]
                if not chunk:
                    out.append("&"); i = j + 1; continue
                b = chunk.replace(",", "/")
                b += "=" * ((4 - len(b) % 4) % 4)
                out.append(base64.b64decode(b).decode("utf-16-be", "ignore"))
                i = j + 1
            else:
                out.append(name[i]); i += 1
        return "".join(out)
    except Exception:
        return name


def _safe_select(m, box_original):
    """IMAP SELECT с экранированием имени папки (ОРИГИНАЛЬНОЕ имя из list)."""
    try:
        typ, data = m.select('"%s"' % box_original.replace('"', '\\"'))
        return typ == "OK"
    except Exception:
        return False


def _safe_text(s):
    """Убирает символы, которые не лезут в cp1251-консоль (эмодзи и т.п.)."""
    if not s:
        return ""
    try:
        s.encode("cp1251")
        return s
    except Exception:
        return "".join(ch if ord(ch) < 0x2500 else "?" for ch in s)


def _site_keywords(url):
    """Ключевые слова сайта для topic-hit.
    Возвращает set: чистый домен (без www/tld) + домен целиком.
    Напр. https://mycoolshop.com/ -> {'mycoolshop', 'mycoolshop.com'}."""
    dom = _domain_from_url(url)
    if not dom:
        return set()
    out = {dom}
    base = dom
    if "." in base:
        base = base.rsplit(".", 1)[0]
    if base.startswith("www."):
        base = base[4:]
    if base:
        out.add(base)
    return out


def build_site_maps():
    """Три мапы из БД (sites, кому уже писали):
      email_map:  email(lower)         -> [site_id]   (точный получатель)
      domain_map: domain сайта(lower)  -> [site_id]   (запасной матч по домену)
      name_map:   keyword(lower)       -> [site_id]   (topic-hit по названию)
    Idempotent: отвеченные (replied/hired) не трогаем при применении, но в мапу
    кладём всех, чтобы цепочка Sent->site работала даже для уже replied.
    """
    conn = gc.get_conn()
    rows = conn.execute(
        "SELECT id, email, url FROM sites WHERE email IS NOT NULL"
    ).fetchall()
    conn.close()
    email_map = {}
    domain_map = {}
    name_map = {}
    for r in rows:
        key = (r["email"] or "").strip().lower()
        if key:
            email_map.setdefault(key, []).append(r["id"])
        dom = _domain_from_url(r["url"])
        if dom:
            domain_map.setdefault(dom, []).append(r["id"])
            for kw in _site_keywords(r["url"]):
                name_map.setdefault(kw, []).append(r["id"])
    return email_map, domain_map, name_map


def build_sent_map(email_addr, password, email_map, min_date=None):
    """Читает папку Sent аккаунта. Для каждого НАШЕГО письма:
      Message-ID -> site_id (по To: -> email_map).
    Возвращает dict: msgid(lower) -> site_id."""
    msg_map = {}
    try:
        socket.setdefaulttimeout(30)
        m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        m.login(email_addr, password)
        boxes = list_mailboxes(m)
        sent_box = None
        for info in boxes.values():
            if "Sent" in info["flags"]:
                sent_box = info["orig"]
                break
        if sent_box is None:
            for cand in _SENT_CANDIDATES:
                if cand in boxes:
                    sent_box = boxes[cand]["orig"]
                    break
        if sent_box is None:
            for dec, info in boxes.items():
                if "sent" in dec.lower() or "отправ" in dec.lower():
                    sent_box = info["orig"]
                    break
        if not sent_box:
            m.logout()
            return msg_map
        gc.log(f"Sent-box dlya {email_addr}: {sent_box}", "RECORDER")
        if not _safe_select(m, sent_box):
            m.logout()
            return msg_map
        since = (min_date or (datetime.now() - timedelta(days=60))).strftime("%d-%b-%Y")
        typ, data = m.search(None, "SINCE", since)
        if typ != "OK" or not data or not data[0]:
            m.logout()
            return msg_map
        nums = data[0].split()
        # Батчевый fetch по диапазонам: Gmail через прокси НЕ ест список
        # "n1 n2 n3" (BAD parse), но ест диапазоны "a:b". По одному fetch
        # на 122+ письма/акк было бы минутами - поэтому режем на чанки.
        batch = 50
        for i in range(0, len(nums), batch):
            chunk = nums[i:i + batch]
            start = int(chunk[0]); end = int(chunk[-1])
            typ, msg_data = m.fetch(f"{start}:{end}", "(RFC822.HEADER)")
            if typ != "OK" or not msg_data:
                continue
            for item in msg_data:
                if not isinstance(item, tuple) or not isinstance(item[1], bytes):
                    continue
                msg = email_lib.message_from_bytes(item[1])
                msgid = (msg.get("Message-ID") or "").strip().lower()
                if not msgid:
                    continue
                to_addrs = extract_emails(msg.get_all("To", []))
                for to in to_addrs:
                    if to in email_map:
                        for sid in email_map[to]:
                            msg_map[msgid] = sid
                        break
        m.logout()
    except Exception as e:
        gc.log(f"Sent-scan oshibka dlya {email_addr}: {e}", "RECORDER")
    return msg_map


def _is_own_account(from_email, accounts):
    return from_email in {a[0].lower() for a in accounts}


def _is_system_junk(from_email):
    local = from_email.partition("@")[0]
    return local in ("noreply", "no-reply", "postmaster", "mailer-daemon", "root")


def _html_to_text(html):
    """Грубый, но надёжный парсер HTML-письма в чистый текст."""
    if not html:
        return ""
    from html import unescape as _unescape
    h = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6])[^>]*>", "\n", html)
    h = re.sub(r"(?i)<[^>]+>", " ", h)
    h = _unescape(h)
    lines = [ln.strip() for ln in h.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return " ".join(text.split())


def _extract_preview(msg):
    """Достаёт ЧИСТЫЙ текст ответа из письма (без лимита длины)."""
    plain, html = "", ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain" and not plain:
                    try:
                        plain = part.get_payload(decode=True).decode("utf-8", "ignore")
                    except Exception:
                        plain = ""
                elif ct == "text/html" and not html:
                    try:
                        html = part.get_payload(decode=True).decode("utf-8", "ignore")
                    except Exception:
                        html = ""
        else:
            try:
                payload = msg.get_payload(decode=True).decode("utf-8", "ignore")
            except Exception:
                payload = ""
            if msg.get_content_type() == "text/html":
                html = payload
            else:
                plain = payload
    except Exception:
        pass
    src = plain.strip() or _html_to_text(html)
    src = src.strip()
    return " ".join(src.split())


def _soft_domain_match(frm_domain, site_domain):
    """Мягкий матч домена: точное, вложенное (поддомен/формы обратной связи),
    или сайт вложен в домен отправителя."""
    frm = (frm_domain or "").lower()
    site = (site_domain or "").lower()
    if not frm or not site:
        return False
    return (frm == site
            or frm.endswith("." + site)
            or site.endswith("." + frm))


def _topic_hit(text, name_map):
    """Ищет ключевое слово сайта (название/домен) в тексте ответа.
    Возвращает site_id или None."""
    if not text:
        return None
    low = text.lower()
    for kw, sids in name_map.items():
        if len(kw) >= 4 and kw in low:
            return sids[0]
    return None


def scan_inbox_for_replies(email_addr, password, msg_map, email_map, domain_map,
                           name_map, accounts, lookback_days=30):
    """Сканирует ВСЕ папки аккаунта на ответы.
    Матч (в порядке приоритета):
      1) In-Reply-To / References содержит наш Message-ID (цепочка) -> site_id
      2) Delivered-To -> наш акк -> его Sent-цепочка (цепочка через доставку)
      3) мягкий домен From ответа == домен сайта -> site_id
      4) topic-hit: название сайта в теме/теле -> site_id
    Возвращает (matched, unmatched):
      matched:  [(site_id, subject, preview, from_email)]
      unmatched:[(from_email, subject, preview)]  — для сохранения в БД
    """
    matched = []
    unmatched = []
    own_addrs = {a[0].lower() for a in accounts}
    try:
        # ТАЙМАУТ 120с (было 30): fetch полных RFC822 из [Gmail]/All Mail
        # (150+ писем) реально занимает >30с через FCC-прокси -> socket.timeout
        # ловился внешним except и обнулял ВЕСЬ скан аккаунта (теряли ответы,
        # напр. mar-1002@mail.ru в All Mail ndnd***).
        socket.setdefaulttimeout(120)
        m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        m.login(email_addr, password)
        boxes = list_mailboxes(m)
        # Сканируем папки-получатели: INBOX, Вся почта (\All), Спам (\Junk),
        # Важное (Important). НЕ трогаем Sent/черновики/корзину - там нет
        # входящих ответов. \All покрывает и forwarded-копии и письма, которые
        # Gmail вынес из INBOX в "Вся почта" (реальный кейс: mar-1002@mail.ru
        # ответила на ndnd***, Gmail убрал из INBOX -> рекордер терял ответ).
        # ВАЖНО: Gmail шлёт флаги в ДВУХ форматах - с обратным слэшем
        # (\\All) и БЕЗ (All). Нормализуем, иначе All Mail/Spam не попадают
        # в выборку на аккаунтах без слэша -> теряем ответы.
        scan = []
        for name, info in boxes.items():
            raw_flags = " ".join(info.get("flags", []))
            # нормализуем: убираем слэш, приводим к верхнему регистру
            norm_flags = {f.upper().lstrip("\\") for f in raw_flags.split()}
            low_name = name.lower()
            is_sent = ("SENT" in norm_flags
                       or "отправленные" in low_name
                       or "sent" in low_name)
            is_drafts = ("DRAFTS" in norm_flags
                         or "черновики" in low_name
                         or "draft" in low_name)
            is_trash = ("TRASH" in norm_flags
                        or "корзина" in low_name
                        or "trash" in low_name)
            if is_sent or is_drafts or is_trash:
                continue
            is_inbox = (name.upper() == "INBOX" or "inbox" in low_name)
            is_recv = (is_inbox
                       or "ALL" in norm_flags
                       or "JUNK" in norm_flags
                       or "IMPORTANT" in norm_flags)
            if is_recv:
                scan.append(info["orig"])
        scan = list(dict.fromkeys(scan)) or ["INBOX"]
        since = (datetime.now() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
        for box in scan:
            try:
                if not _safe_select(m, box):
                    gc.log(f"Inbox-scan: ne udalos vybrat papku {box} (ak {email_addr})", "RECORDER")
                    continue
                typ, data = m.search(None, "SINCE", since)
                if typ != "OK" or not data or not data[0]:
                    continue
                nums = data[0].split()
                for i in range(0, len(nums), 50):
                    chunk = nums[i:i + 50]
                    start = int(chunk[0]); end = int(chunk[-1])
                    typ, msg_data = m.fetch(f"{start}:{end}", "(RFC822)")
                    if typ != "OK" or not msg_data:
                        continue
                    for item in msg_data:
                        if not isinstance(item, tuple) or not isinstance(item[1], bytes):
                            continue
                        msg = email_lib.message_from_bytes(item[1])
                        from_email = extract_emails(msg.get("From", ""))
                        from_email = from_email[0] if from_email else ""
                        # пропускаем собственные аккаунты (наши же письма в "Вся почта")
                        if from_email in own_addrs:
                            continue
                        if _is_system_junk(from_email):
                            continue
                        subject = _decode_header(msg.get("Subject", ""))
                        # --- цепочка: In-Reply-To + References ---
                        refs = []
                        irt = msg.get("In-Reply-To")
                        if irt:
                            refs += re.findall(r"<([^>]+)>", irt)
                        refs_field = msg.get("References")
                        if refs_field:
                            refs += re.findall(r"<([^>]+)>", refs_field)
                        refs_lower = [r.strip().lower() for r in refs]
                        sid = None
                        for r in refs_lower:
                            if r in msg_map:
                                sid = msg_map[r]
                                break
                        # --- Delivered-To: на какой из наших акков доставлено ---
                        if sid is None:
                            for dt in extract_emails(msg.get_all("Delivered-To", [])):
                                dt = dt.strip().lower()
                                if dt in own_addrs and dt in email_map:
                                    # ищем site_id по To: акка, на который пришло
                                    for cand in email_map.get(dt, []):
                                        sid = cand
                                        break
                                if sid is not None:
                                    break
                        # --- мягкий домен ---
                        if sid is None:
                            fdom = _domain_of(from_email)
                            for sdom, sids in domain_map.items():
                                if _soft_domain_match(fdom, sdom):
                                    sid = sids[0]
                                    break
                        # --- topic-hit по названию сайта в теме/теле ---
                        if sid is None:
                            body = _extract_preview(msg)
                            sid = _topic_hit(subject + " " + body, name_map)
                        if sid is None:
                            preview = _extract_preview(msg)
                            unmatched.append((from_email, subject, preview))
                            continue
                        preview = _extract_preview(msg)
                        matched.append((sid, subject, preview, from_email))
            except Exception as e:
                # Падение ОДНОЙ папки/письма не должно убивать весь скан
                # аккаунта (было: socket.timeout на All Mail -> теряли все
                # ответы этого аккаунта). Логируем и идём дальше.
                gc.log(f"Inbox-scan: oshibka v papke {box} (ak {email_addr}): {e}", "RECORDER")
                continue
        m.logout()
    except Exception as e:
        gc.log(f"Inbox-scan oshibka dlya {email_addr}: {e}", "RECORDER")
        gc.record_pitfall(
            "Recorder: oshibka IMAP",
            str(e),
            "IMAP vyklyuchen v akkaunte / nevernyy app-password / blok",
            "vklyuchit IMAP v nastroykah Gmail, proverit APP_PASSWORD"
        )
    return matched, unmatched


def _existing_reply_fingerprints(sid):
    """Отпечатки (subject, preview) уже сохранённых REPLY:: для сайта."""
    try:
        conn = gc.get_conn()
        row = conn.execute("SELECT notes FROM sites WHERE id=?", (sid,)).fetchone()
        conn.close()
    except Exception:
        return set()
    if not row or not row["notes"]:
        return set()
    out = set()
    for line in (row["notes"] or "").splitlines():
        line = line.strip()
        if not line.startswith("REPLY::"):
            continue
        parts = line[len("REPLY::"):].split(" | ", 2)
        if len(parts) >= 3:
            out.add((parts[0].strip(), parts[2].strip()))
    return out


def _extend_reply_if_truncated(site_id, subject, body):
    """Дописывает/заменяет обрезанный REPLY:: полным текстом, если новый длиннее.
    Дублей не создаёт. Возвращает True если заменён/дописан.
    Используется fetch_missing_replies.py для точечной дозаливки полных ответов."""
    try:
        conn = gc.get_conn()
        row = conn.execute("SELECT notes FROM sites WHERE id=?", (site_id,)).fetchone()
        notes = row["notes"] if row and row["notes"] else ""
        subj_d = _safe_text(subject).strip()
        body_clean = " ".join((body or "").split())
        if not subj_d or not body_clean:
            conn.close()
            return False
        lines = notes.splitlines()
        replaced = False
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s.startswith("REPLY::"):
                continue
            parts = s[len("REPLY::"):].split(" | ", 2)
            if len(parts) < 3:
                continue
            if parts[0].strip() == subj_d and len(body_clean) > len(parts[2].strip()):
                lines[i] = f"REPLY:: {parts[0]} | {parts[1]} | {body_clean}"
                replaced = True
                break
        if replaced:
            conn.execute("UPDATE sites SET notes=? WHERE id=?",
                         ("\n".join(lines), site_id))
            conn.commit()
        conn.close()
        return replaced
    except Exception as e:
        gc.log(f"extend_reply oshibka (#{site_id}): {e}", "RECORDER")
        return False


def _store_unmatched(unmatched, dry_run):
    """Сохраняет не-matчнутые входящие в inbound_unmatched (дедуп).
    Возвращает число реально добавленных строк."""
    if not unmatched:
        return 0
    added = 0
    try:
        conn = gc.get_conn()
        existing = set()
        for r in conn.execute(
            "SELECT from_email, subject, preview FROM inbound_unmatched"
        ).fetchall():
            existing.add((r["from_email"], r["subject"], r["preview"]))
        for from_email, subject, preview in unmatched:
            fp = (from_email, subject, preview)
            if fp in existing:
                continue
            if dry_run:
                added += 1
                continue
            conn.execute(
                "INSERT INTO inbound_unmatched (site_id, from_email, subject, preview, status) "
                "VALUES (NULL, ?, ?, ?, 'new')",
                (from_email, subject, preview),
            )
            added += 1
        if not dry_run:
            conn.commit()
        conn.close()
    except Exception as e:
        gc.log(f"store_unmatched oshibka: {e}", "RECORDER")
    return added


def apply(id_, status, amount=None, note=None):
    if status == "replied":
        subprocess.run([sys.executable, TRACK, "reply", str(id_)],
                       capture_output=True, text=True, timeout=30)
    elif status == "hired":
        cmd = [sys.executable, TRACK, "hired", str(id_)]
        if amount:
            cmd += ["--amount", str(amount)]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    elif status == "rejected":
        subprocess.run([sys.executable, TRACK, "rejected", str(id_)],
                       capture_output=True, text=True, timeout=30)
    if note:
        subprocess.run([sys.executable, TRACK, "note", str(id_), note],
                       capture_output=True, text=True, timeout=30)
    gc.log(f"Reshenie po #{id_}: {status}" + (f" (+{amount} BYN)" if amount else ""), "RECORDER")


def main():
    env = gc.load_env()
    dry_run = "--dry-run" in sys.argv

    # --- РУЧНОЙ режим: gordon_responses.txt (пропускаем в dry-run) ---
    if os.path.exists(RESPONSES):
        with open(RESPONSES, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"^(\d+)\s+(replied|hired|rejected)\s*(\d+(?:\.\d+)?)?", line)
                if m:
                    id_ = int(m.group(1))
                    status = m.group(2)
                    amount = float(m.group(3)) if m.group(3) else None
                    if dry_run:
                        gc.log(f"[DRY] ruchnoy: #{id_} {status}", "RECORDER")
                        continue
                    apply(id_, status, amount)
                else:
                    gc.log(f"Ne raspoznal stroku: {line}", "RECORDER")
        if not dry_run:
            open(RESPONSES, "w", encoding="utf-8").close()
            gc.log("gordon_responses.txt obrabotan i ochishen.", "RECORDER")
    else:
        gc.log("Net ruchnyh otvetov (gordon_responses.txt pust).", "RECORDER")

    # --- АВТО-режим: IMAP-поллинг (включается IMAP_ENABLED=true) ---
    if env.get("IMAP_ENABLED", "false").lower() != "true":
        gc.log("IMAP vyklyuchen (IMAP_ENABLED=false). Avto-proverka propuschena.", "RECORDER")
        return

    gc.log("IMAP- pollling VKLYUCHEN. Proveryaem otvety po cepochke pisem...", "RECORDER")
    accounts = get_accounts(env)
    if not accounts:
        gc.log("Net akkauntov (ACCOUNT_x) v .env. Stop.", "RECORDER")
        return

    email_map, domain_map, name_map = build_site_maps()
    if not email_map:
        gc.log("Net otpravlennyh saitov dlya proverki otvetov.", "RECORDER")
        return

    lookback = int(env.get("IMAP_LOOKBACK_DAYS", "14"))
    try:
        conn = gc.get_conn()
        row = conn.execute(
            "SELECT MIN(created_at) AS oldest FROM sites WHERE status IN (%s)"
            % ",".join("?" * len(_SENT_LIKE)), _SENT_LIKE
        ).fetchone()
        conn.close()
        if row and row["oldest"]:
            oldest = datetime.strptime(row["oldest"][:10], "%Y-%m-%d")
            age = (datetime.now() - oldest).days + 3
            lookback = max(lookback, age)
    except Exception:
        pass

    # ЭТАП 1: мапа Message-ID -> site_id из Sent ВСЕХ аккаунтов
    msg_map = {}
    for email_addr, password in accounts:
        sm = build_sent_map(email_addr, password, email_map,
                            min_date=datetime.now() - timedelta(days=lookback))
        msg_map.update(sm)
    gc.log(f"Sent-map postroena: {len(msg_map)} nashih pisem s Message-ID.", "RECORDER")

    # ЭТАП 2+3: сканируем входящие, матчим, сохраняем unmatched
    found = set()
    seen_unmatched = set()
    total_unmatched = 0
    for email_addr, password in accounts:
        matched, unmatched = scan_inbox_for_replies(
            email_addr, password, msg_map, email_map, domain_map, name_map,
            accounts, lookback
        )
        for sid, subject, preview, from_email in matched:
            if sid in found:
                continue
            conn = gc.get_conn()
            st = conn.execute("SELECT status FROM sites WHERE id=?", (sid,)).fetchone()
            conn.close()
            if st and st["status"] in ("hired", "rejected"):
                found.add(sid)
                continue
            found.add(sid)
            subj_d = _safe_text(subject).strip()
            prev = preview.strip()
            # Мусор-маркеры ищем И в теме, И в теле (webpay-письма держат
            # "WEBPAY support message" в subject, а тело чистое -> иначе
            # мусор проскакивал в replied, затирая реальный статус сайта).
            junk_markers = ("webpay support message", "payment", "transaction receipt")
            hay = (subj_d + " " + prev).lower()
            if any(mk in hay for mk in junk_markers):
                gc.log(f"PROPUSK musora po #{sid}: '{subj_d[:60]}'", "RECORDER")
                continue
            existing = _existing_reply_fingerprints(sid)
            if (subj_d, prev) in existing:
                gc.log(f"DUPL otveta po #{sid} propuschen (uzhe est).", "RECORDER")
                continue
            gc.log(f"NAYDEN otvet po #{sid} (ot {from_email}): '{subj_d}'", "RECORDER")
            if not dry_run:
                apply(sid, "replied",
                      note=f"REPLY::{subj_d} | {from_email} | {prev}")
        for from_email, subject, preview in unmatched:
            key = (from_email, subject, preview)
            if key in seen_unmatched:
                continue
            seen_unmatched.add(key)
            total_unmatched += 1
            gc.log(f"NE-matchnuto (sohranim v inbound_unmatched): {from_email} | "
                   f"'{_safe_text(subject)}'", "RECORDER")

    # ЭТАП 3: сохраняем unmatched в БД (дедуп внутри _store_unmatched)
    stored = _store_unmatched(list(seen_unmatched), dry_run)

    if found:
        gc.log(f"IMAP: otmecheno replied: {sorted(found)}", "RECORDER")
    else:
        gc.log("IMAP: novyh otvetov (matched) net.", "RECORDER")
    if dry_run:
        gc.log(f"[DRY-RUN] matched={len(found)} unmatched_vhodyaschih={total_unmatched} "
               f"(budet dobavleno v inbound_unmatched: {stored}). ZAPIS NE PROIZVEDENA.",
               "RECORDER")
    else:
        gc.log(f"IMAP: sohraneno v inbound_unmatched: {stored} novyh.", "RECORDER")


if __name__ == "__main__":
    main()
