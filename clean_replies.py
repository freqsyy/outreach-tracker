"""Очистка поля notes у replied/hired от грязи рекордера.

Проблемы, которые чиним:
- дубли одного и того же авто-ответа (склеены N раз)
- HTML-теги в теле письма
- не декодированные MIME-заголовки (?=utf-8?B?...?= / ?q?)
- левый мусор (WEBPAY support message / платёжки) ошибочно матчился как ответ

Результат: каждый реальный ответ -> одна строка "REPLY:: <subject> | <from> | <чистый текст>".
Мусорные "ответы" (платёжки) убираются, статус сайта возвращается в sent.

USAGE: python clean_replies.py [--apply]
"""
import sqlite3
import re
import sys
from email.header import make_header, decode_header
from html import unescape as html_unescape

DB = "outreach.db"
JUNK = ("webpay support message", "payment receipt", "transaction receipt", "ваш счёт",
        "счет на оплату")


def decode_mime(val):
    if not val:
        return ""
    try:
        return str(make_header(decode_header(val)))
    except Exception:
        return val


def html2text(html):
    if not html:
        return ""
    h = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6])[^>]*>", "\n", html)
    h = re.sub(r"(?i)<[^>]+>", " ", h)
    h = html_unescape(h)
    lines = [l.strip() for l in h.splitlines()]
    return "\n".join(l for l in lines if l)


def clean_body(s):
    if not s:
        return ""
    # тело может состоять из склеенных MIME-кусков (?=utf-8?q?...?=) - декодим целиком
    if "?=" in s:
        s = decode_mime(s)
    # если остался HTML -> в текст
    if "<" in s and ">" in s:
        s = html2text(s)
    else:
        s = html_unescape(s)
    return " ".join(s.split())


def parse_old_note(notes):
    """Возвращает список (subject, frm, body) из старых строк Avto-otvet:/REPLY::/CLIENT::."""
    out = []
    for line in (notes or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("Avto-otvet:"):
            body = line[len("Avto-otvet:"):].strip()
            if " | " in body:
                subj, _, rest = body.partition(" | ")
            else:
                subj, rest = body, ""
            out.append((decode_mime(subj).strip(), "", clean_body(rest)))
        elif line.startswith("REPLY::"):
            body = line[len("REPLY::"):].strip()
            parts = body.split(" | ", 2)
            if len(parts) >= 3:
                out.append((parts[0].strip(), parts[1].strip(), clean_body(parts[2])))
            elif len(parts) == 2:
                out.append((parts[0].strip(), "", clean_body(parts[1])))
            else:
                out.append((body.strip(), "", ""))
        elif line.startswith("CLIENT::replied::"):
            # CLIENT::replied:: <timestamp> :: <text>
            _, _, rest = line.partition("::")
            _, _, text = rest.partition("::")
            out.append(("Re: ответ клиента", "", clean_body(text)))
        # прочее (AUDIT::, описание сайта) - не трогаем
    return out


def main():
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, url, email, status, notes FROM sites "
        "WHERE status IN ('replied','hired')"
    ).fetchall()

    resets = []  # (id,) сайтов, у которых после чистки ответов не осталось -> sent
    for r in rows:
        notes = r["notes"] or ""
        parsed = parse_old_note(notes)
        # отбрасываем мусорные "ответы" (платёжки)
        real = []
        for subj, frm, body in parsed:
            low = (subj + " " + body).lower()
            if any(j in low for j in JUNK):
                print(f"  [MUSOR] #{r['id']} {r['url']}: drop '{subj[:50]}'")
                continue
            real.append((subj, frm, body))

        # дедуп по (subject, body)
        seen, deduped = set(), []
        for subj, frm, body in real:
            fp = (subj, body)
            if fp in seen:
                continue
            seen.add(fp)
            deduped.append((subj, frm, body))

        # дедуп по subject (нормализуем пробелы/регистр): оставляем САМУЮ
        # ДЛИННУЮ строку (полный ответ), короткие обрезки выкидываем
        best = {}
        order = []
        for s, f, b in deduped:
            key = s.strip().casefold()
            cand = (s.strip(), f.strip(), b)
            if key not in best or len(cand[2]) > len(best[key][2]):
                if key not in best:
                    order.append(key)
                best[key] = cand
        new_lines = [f"REPLY:: {best[k][0]} | {best[k][1]} | {best[k][2]}" for k in order]

        # сохраняем нетронутыми прочие строки (AUDIT::/CLIENT::/описание)
        other = [ln.strip() for ln in notes.splitlines()
                 if ln.strip() and not ln.strip().startswith(("Avto-otvet:", "REPLY::"))]
        final = other + new_lines
        new_notes = "\n".join(final)

        if apply:
            conn.execute("UPDATE sites SET notes=? WHERE id=?", (new_notes, r["id"]))

        print(f"#{r['id']} {r['url']}  replies={len(deduped)} dropped_musor={len(parsed)-len(real)}")
        for s, f, b in deduped:
            print(f"    • {s}\n      {b[:160]}")

        if not deduped and r["status"] == "replied":
            resets.append((r["id"], r["url"]))

    if resets:
        print(f"\n[RESET] сайтов без реальных ответов (были только платёжки): {len(resets)}")
        for i, u in resets:
            print(f"    #{i} {u} -> sent")
            if apply:
                conn.execute("UPDATE sites SET status='sent' WHERE id=?", (i,))

    if apply:
        conn.commit()
        print("\nAPPLIED. Закоммичено.")
    else:
        print("\nDRY-RUN. Чтобы применить: python clean_replies.py --apply")
    conn.close()


if __name__ == "__main__":
    main()
