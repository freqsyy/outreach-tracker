"""
common_contacts.py — ОБЩИЙ модуль извлечения контактов армии (Single Source of Truth).

Объединяет разрозненную логику, которую ДУБЛИРОВАЛИ:
  - outreach-tracker/gordon_common.py  -> extract_contacts(html) -> (emails, tgs)
  - outreach-tracker/agent_scout.py    -> is_junk_email / pick_contact
  - nova/nova_parser.py               -> EMAIL_RE / TG_RE / PHONE_RE / _email_ok / _phone_ok

Финальный единый набор regex + фильтров. Агенты (scout / parser / nova_auditor)
должны ДЕЛЕГИРОВАТЬ сюда, а не держать свои копии (устраняет drift).

SECURITY: модуль сам НЕ ходит в сеть. Только парсит переданный HTML.
            SSRF-гарды — задача вызывающего агента (is_safe_host/fetch_url).

ONE-WRITER: модуль НИЧЕГО не пишет в БД/git. Только pure-функции.
"""

import re

# --- единый regex-набор (выверен по nova + scout) -------------------------
# email: user@domain.tld (re.I — ловит и Cyrillic-friendly ascii-домены)
EMAIL_RE = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", re.I)

# t.me/<handle> ИЛИ @handle, но @ НЕ внутри email (предшествующий \w или .
# = локальная часть мыла, а не телега)
TG_RE = re.compile(r"t\.me/([a-z0-9_]{4,32})|(?<![\w.])@([a-z0-9_]{4,32})", re.I)

# телефон: + и/или цифры с разделителями, от 8 до 15 цифр в сумме
PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-()]{7,}\d)")

# --- блок-листы (мусорные/placeholder email и ложные @-хэндлы) ----------
# мусорные домены (placeholder/тестовые)
JUNK_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "dev.io",
                      "test.com", "localhost", "invalid"}

# мусорные локальные части
JUNK_EMAIL_LOCAL = {"you", "your", "test", "noreply", "no-reply", "admin@example"}

# ложные @-хэндлы (CSS/JSON-LD/почтовые домены) — их ловит TG_RE, отсекаем
_TG_STOP = {"font", "fonts", "import", "media", "charset", "keyframes",
            "include", "apply", "supports", "document", "page", "namespace",
            "gmail", "mail", "yandex", "yahoo", "outlook", "hotmail",
            "proton", "icloud", "bk", "list", "rambler", "inbox",
            "type", "context", "graph", "id", "schema",
            "example", "domain", "email", "test", "your", "user", "admin",
            "noreply", "no", "sample", "placeholder", "here", "me",
            "theme", "themes", "style", "color", "colors", "config",
            "settings", "modal", "button", "header", "footer"}

# плейсхолдер-email (user@domain.com, your@email.com ...) — не реальные
_EMAIL_PLACEHOLDER = ("example", "domain.com", "email.com", "test", "user@",
                        "your@", "admin@", "localhost", "invalid", "sample",
                        "placeholder", "changeme", "no-reply")


# ---------------------------------------------------------------------------
# ОСНОВНОЙ API
# ---------------------------------------------------------------------------
def extract_contacts(html):
    """Единая точка входа. Из HTML -> (emails, tgs, phones).

    - emails: set() очищенных email (lower).
    - tgs: set() хэндлов '@handle' (отфильтрованы _TG_STOP).
    - phones: set() «похожих на телефон» строк (через _phone_ok).
    """
    emails = set()
    for m in EMAIL_RE.findall(html):
        emails.add(m.lower())

    tgs = set()
    for m in TG_RE.findall(html):
        handle = m[0] or m[1]
        if handle and handle.lower() not in _TG_STOP:
            tgs.add("@" + handle)

    phones = set()
    for raw in PHONE_RE.findall(html):
        if _phone_ok(raw):
            phones.add(raw.strip())

    return emails, tgs, phones


# ---------------------------------------------------------------------------
# ФИЛЬТРЫ (делегируются агентами)
# ---------------------------------------------------------------------------
def is_junk_email(email):
    """True, если email — мусорный/placeholder. Используют scout/parser."""
    e = (email or "").lower()
    local, _, dom = e.partition("@")
    if dom in JUNK_EMAIL_DOMAINS:
        return True
    if local in JUNK_EMAIL_LOCAL:
        return True
    if e.endswith("@example.com") or "example" in dom:
        return True
    return False


def _email_ok(addr):
    """Алиас nova._email_ok: отбрасываем плейсхолдер-адреса."""
    a = (addr or "").lower()
    return not any(p in a for p in _EMAIL_PLACEHOLDER)


def pick_contact(emails, site_domain):
    """Из набора email выбирает лучший: сначала на домене сайта,
    потом любой не-мусорный. Возвращает (email, quality) где quality в
    ('domain', 'any', '').

    ВАЖНО: is_junk_email здесь НЕ вызываем — оставляем решение за
    вызывающим (scout хочет видеть все, parser — чистые). Чтобы не
    сломать поведение scout, возвращаем и junk, помечая quality=''.
    Для «чистого» выбора используйте pick_contact_clean().
    """
    if not emails:
        return None, ""
    same_domain = [e for e in emails
                   if e.lower().split("@")[-1] in (site_domain, "www." + site_domain)
                   or e.lower().endswith("." + site_domain)]
    if same_domain:
        return sorted(same_domain)[0], "domain"
    return sorted(emails)[0], "any"


def pick_contact_clean(emails, site_domain):
    """Как pick_contact, но отбрасывает is_junk_email. Для parser/nova."""
    clean = [e for e in emails if not is_junk_email(e)]
    if not clean:
        return None, ""
    same_domain = [e for e in clean
                   if e.lower().split("@")[-1] in (site_domain, "www." + site_domain)
                   or e.lower().endswith("." + site_domain)]
    if same_domain:
        return sorted(same_domain)[0], "domain"
    return sorted(clean)[0], "any"


def _phone_ok(raw):
    """Телефон должен выглядеть как телефон, а не как ID/трекинг-пиксель.
    Голые одиночные цифры ('1 1 0 0 0 13 10') — мусор."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 9 or len(digits) > 15:
        return False
    groups = [g for g in re.split(r"[^\d]", raw) if g]
    if any(len(g) == 1 for g in groups):
        return False  # изолированные одиночные цифры = мусор
    return True


# ---------------------------------------------------------------------------
# UNIT-TEST (5 кейсов) — запуск: python common_contacts.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    def _ok(name, cond):
        print(("PASS" if cond else "FAIL") + " - " + name)

    html = """
    Contact us: hello@myshop.com or @realadmin on Telegram.
    Sales: sales@shop.com, phone +375 29 123-45-67.
    Bug report: noreply@example.com (ignore), CSS @font-face in style.
    Placeholder: your@email.com, t.me/fakehandle, visit example.com.
    """
    emails, tgs, phones = extract_contacts(html)

    _ok("email: real shop caught", "sales@shop.com" in emails)
    _ok("email: hello caught", "hello@myshop.com" in emails)
    _ok("tg: realadmin caught", "@realadmin" in tgs)
    _ok("tg: font STOP filtered", "@font" not in tgs)
    _ok("phone: by-format caught",
        any("375291234567" in p.replace(" ", "").replace("-", "") for p in phones))
    _ok("email: junk flagged", is_junk_email("noreply@example.com"))
    _ok("email: placeholder flagged", is_junk_email("your@email.com"))
    best, q = pick_contact_clean(emails, "myshop.com")
    _ok("pick: domain preferred", best == "hello@myshop.com" and q == "domain")
    print("--- 5-core-check done ---")
