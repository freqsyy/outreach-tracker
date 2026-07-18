#!/usr/bin/env python3
"""
gordon_common.py — общий модуль для всех агентов Гордона.

Один источник правды: база outreach.db (через track.py) + настройки из .env.
Только стандартная библиотека Python (как и в track.py).
"""

import os
import re
import random
import hashlib
import sqlite3
from datetime import datetime, timedelta

# --- Пути ---
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "outreach.db")
ENV_PATH = os.path.join(HERE, ".env")
LOG_PATH = os.path.join(HERE, "gordon_run.log")
PITFALLS_PATH = os.path.join(HERE, "gordon_pitfalls.md")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# --- Грязные контакты: парсер часто хватает CSS-селекторы/плейсхолдеры вместо
# реальных email (sprite@x2.png, example@mail.ru, %20don.alfa-k@yandex.ru).
# Этот фильтр централизован: питает И parser, И scout (оба зовут extract_contacts).
# После фикса баунс-воронка чистая, deliverability растёт.
_PLACEHOLDER_DOMAINS = {
    "example.com", "example.net", "example.org",
    "test-new-site-12345.by", "localhost", "test.com", "abc.com",
}
_PLACEHOLDER_LOCAL = {
    "example", "test", "demo", "user", "foo", "bar",
    "noreply", "no-reply", "noreply", "postmaster", "root",
}
# мусорные хвосты в домене (картинки/спрайты вместо TLD)
_IMG_DOMAIN_RE = re.compile(
    r"\.(png|jpe?g|webp|gif|svg|bmp|ico)$", re.I
)
# мусорные куски в локальной части (x2 / 2x / px / .png прямо в ящике)
_IMG_LOCAL_RE = re.compile(r"(^|[\w.\-+])+(2x|x\d|\dx|\d+px|\.png|\.jpg|\.webp|\.svg)", re.I)
# ЖЁСТКАЯ валидация: любая строка, заканчивающаяся на расширение картинки,
# игнорируется ЦЕЛИКОМ — даже если перед расширением стоит @ (формат
# @file.png, name@host/sprite.png и т.п.). Письма на такие адреса не шлём.
_IMG_TRAILING_RE = re.compile(
    r"\.(png|jpg|jpeg|gif|svg)$", re.I
)


def is_valid_email(email):
    """True только для похожего на реальный контакт email.
    Отсекает: плейсхолдеры, картинки/спрайты, %20, пробелы, домены-заглушки.
    Сохраняет легит admin@ / info@ / sales@ и т.п."""
    e = (email or "").strip().replace("%20", "").strip().lower()
    if not e or "%20" in e or " " in e:
        return False
    if not EMAIL_RE.fullmatch(e):
        return False
    # ЖЁСТКО: строка заканчивается на расширение картинки -> не email, игнор.
    if _IMG_TRAILING_RE.search(e):
        return False
    local, _, dom = e.rpartition("@")
    if not local or not dom:
        return False
    if dom in _PLACEHOLDER_DOMAINS:
        return False
    if local in _PLACEHOLDER_LOCAL:
        return False
    if _IMG_DOMAIN_RE.search(dom):
        return False
    if _IMG_LOCAL_RE.search(local):
        return False
    return True
TG_RE = re.compile(r"t\.me/([a-zA-Z0-9_]+)|@([a-zA-Z0-9_]{4,32})")


def log(msg, agent="GORDON"):
    """Пишет строку в gordon_run.log с меткой времени."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{agent}] {msg}\n"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # лог не должен ломать агента
    # принудительно utf-8, чтобы кириллица не плыла в cp1251-терминале
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(line, end="")


def load_env():
    """Читает .env вручную (без внешних библиотек).
    Возвращает dict. Поддерживает APP_PASSWORD_x=... построчно."""
    env = {}
    if not os.path.exists(ENV_PATH):
        return env
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip()
    return env


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def record_pitfall(title, error, cause, solution):
    """Дописывает грабль в gordon_pitfalls.md (карта камней по методу Назара)."""
    header = "## " + title
    block = (
        f"\n{header}\n"
        f"- Ошибка: {error}\n"
        f"- Причина: {cause}\n"
        f"- Решение: {solution}\n"
        f"- Замечено: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    )
    try:
        existing = ""
        if os.path.exists(PITFALLS_PATH):
            with open(PITFALLS_PATH, "r", encoding="utf-8") as f:
                existing = f.read()
        if title not in existing:
            with open(PITFALLS_PATH, "a", encoding="utf-8") as f:
                f.write(block)
            log(f"Grabel zafiksirovan: {title}", "PITFALL")
    except Exception as e:
        log(f"Не удалось записать pitfall: {e}", "PITFALL")


def extract_contacts(html):
    """Из HTML вытаскивает (emails, telegrams).
    Email фильтруются через is_valid_email — отсекаем CSS-мусор/плейсхолдеры."""
    emails = set()
    for m in EMAIL_RE.findall(html):
        if is_valid_email(m):
            emails.add(m.lower())
    tgs = set()
    for m in TG_RE.findall(html):
        handle = m[0] or m[1]
        if handle:
            tgs.add("@" + handle)
    return emails, tgs


# Путь к письму (UTF-8, нормальная кириллица — не транслит)
LETTER_PATH = os.path.join(HERE, "letter.txt")


def load_letter():
    """Читает письмо из letter.txt.
    Формат:
      Subject: <тема>
      <пустая строка>
      <тело>
    Возвращает (subject, body)."""
    subject = "Предложение по тестированию вашего сайта"
    body = ""
    try:
        with open(LETTER_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        if lines and lines[0].lower().startswith("subject:"):
            subject = lines[0].split(":", 1)[1].strip()
            body = "\n".join(lines[2:])  # пропускаем Subject и пустую строку
    except Exception as e:
        log(f"Не удалось прочитать letter.txt: {e}", "COMMON")
    return subject, body


# --- Извлечение бага из notes для персонализации письма (v0.4 core-funnel) ---

# --- ДИНАМИЧЕСКАЯ ГЕНЕРАЦИЯ ПИСЕМ (ротация шаблонов, без вызова к ИИ) ---
# Причина: в коде агентов нет LLM-клиента; FCC-прокси трогать нельзя.
# 20-30 шаблонов + рандом приветствий/CTA/структуры дают ~90% уникальности
# текста -> обход спам-фильтров, без трат и сетевой зависимости.
LETTERS_DIR = os.path.join(HERE, "letters")
TG_HANDLE = "@oojdo"  # контакт Назара (из letter.txt)

# Пулы вариативности. Пустая строка в GREETINGS = письмо БЕЗ приветствия.
GREETINGS = ["Привет", "Здравствуйте", "Добрый день", "Доброго дня",
             "Приветствую", "Хай", ""]
SUBJECT_POOL = [
    "Нашёл баг на {site} - показать и помочь починить?",
    "По поводу {site}",
    "{site}: нашёл, что мешает конверсии",
    "Привет из аудита {site}",
    "Тестирование {site}: нашёл критический момент",
    "Почему на {site} уходят заявки?",
    "{site}: глюк на телефоне",
    "Небольшая находка по {site}",
    "{site}: нашёл баг, который вас тормозит",
    "Как {site} теряет деньги (нашёл на аудите)",
    "Пара минут на {site} - и нашёл баг",
    "Честно про {site}",
    "{site} - быстрый аудит",
    "История про {site}",
    "{site}: баг, который легко починить",
    "Зеркало для {site}",
    "{site}: это отпугивает ваших клиентов",
    "Нашёл, как улучшить {site}",
    "{site} - коротко про баг",
    "Где {site} теряет посетителей",
    "{site}: взгляд QA-щика",
    "Боль {site} - нашёл",
    "{site}: мелочь, а важно",
    "Про {site}",
]
CTA_POOL = [
    "Предлагаю сделать полный аудит сайта",
    "Могу прогнать {site} целиком и собрать отчёт",
    "Готов разобрать проект полностью",
    "Могу пройтись по всему сайту и составить отчёт с багами",
    "Предлагаю сотрудничество: прогоняю сайт и показываю находки",
    "Сделаю аудит и дам план исправлений",
    "Могу помочь закрыть это и смежные шероховатости",
    "Разберу весь путь клиента по сайту и покажу дыры",
    "Соберу все проблемные точки в один отчёт с фиксами",
    "Покажу не только этот баг, но и что ещё стоит подтянуть",
]

# Карта тегов -> читаемая ниша (для {niche} в письме)
_NICHE_MAP = {
    "ecommerce": "интернет-магазин", "shop": "интернет-магазин",
    "commerce": "интернет-магазин", "store": "интернет-магазин",
    "clothing": "одежда", "fashion": "одежда/мода", "women": "женская одежда",
    "cosmetics": "косметика", "beauty": "косметика/бьюти",
    "handmade": "хендмейд", "soap": "мыло/косметика",
    "aromatherapy": "ароматерапия", "tea": "чай", "coffee": "кофе",
    "cafe": "кафе/кофейня", "food": "еда/доставка", "restaurant": "ресторан",
    "game": "игры", "gamedev": "игры", "gaming": "игры",
    "design": "дизайн", "studio": "студия", "photo": "фотография",
    "realty": "недвижимость", "travel": "путешествия", "tour": "туризм",
    "health": "здоровье", "fitness": "фитнес", "medical": "медицина",
    "education": "обучение", "course": "курсы", "blog": "блог",
    "service": "услуги", "tools": "инструменты", "app": "приложение",
    "saas": "сервис", "ai": "AI-сервис", "finance": "финансы",
    "crypto": "крипта", "news": "медиа/новости", "auto": "авто",
    "construction": "строительство", "remont": "ремонт", "law": "юриспруденция",
    "belarus": "белорусский проект", "russia": "российский проект",
    "by": "белорусский проект",
}


def detect_niche(tags):
    """По tags (через запятую) возвращает читаемую нишу для письма.
    Если не ясно — возвращает 'сайт' (безопасно, не ломает плейсхолдер)."""
    if not tags:
        return "сайт"
    tags_l = [t.strip().lower() for t in str(tags).split(",") if t.strip()]
    for t in tags_l:
        if t in _NICHE_MAP:
            return _NICHE_MAP[t]
    # совпадение по подстроке (напр. 'ecommerce-by')
    for t in tags_l:
        for key, val in _NICHE_MAP.items():
            if key in t and key not in ("by",):
                return val
    return "сайт"


# --- Перевод технического бага на человеческий язык (для владельца) ---
# Аудитор пишет ТОЧНЫЙ баг в notes/audits (нужен для фикса). В письмо идёт
# "человеческий" вариант, чтобы обычный владелец понял и не удалил письмо.
# СТРАТЕГИЯ: не переписываем фразу пословно (ломает падежи), а ВЫЧИЩАЕМ
# тех-жаргон и заменяем англо-термины на бытовые. Русский текст аудитора
# ("Кнопка перекрыта другим элементом") оставляем как есть — он уже понятен.
_BUG_HUMAN_RULES = [
    # (паттерн, замена) — ПОРЯДОК ВАЖЕН: составные англо-фразы первыми.
    (r"улетел[а]? за пределы viewport|вылетел[а]? за пределы viewport|"
     r"за пределами viewport|за пределы viewport|out of viewport",
     "уезжает за край экрана"),
    (r"horizontal scroll|горизонтальный скролл", "лишний скролл вбок"),
    (r"console error", "ошибка в браузере"),
    (r"broken link", "ссылка не работает"),
    (r"image not loaded|img not loaded", "картинка не показывается"),
    (r"not found\b", "страница не открывается"),
    (r"click not fired|not clickable|не кликабел",
     "не нажимается"),
    # одиночные англо-термины -> бытовые (границы слов)
    (r"\bviewport\b", "край экрана"),
    (r"\boverlay\b", "всплывающее окно"),
    (r"\bz-index\b", "слой"),
    (r"\bplaceholder\b", "подсказка в поле"),
    (r"\bmobile\b", "на телефоне"),
    (r"\b404\b", "страница не открывается"),
    # чисто структурные теги/атрибуты как отдельные токены -> убрать
    (r"\bdiv\b|\bspan\b|\bimg\b|\binput\b|\bsection\b|"
     r"\bheader\b|\bfooter\b|\bnav\b", " "),
    (r"\bbtn\b|\bbutton\b", "кнопка"),
    (r"\bselector\b|\bcss\b|\bxpath\b", " "),
    (r"\bСелектор\b", " "),
]


def humanize_bug(bug):
    """Переводит технический баг в понятный владельцу вид.
    Никогда не бросает исключение. Если на входе пусто — пусто.
    Стратегия: убираем CSS-селекторы/теги, заменяем тех-термины на бытовые,
    схлопываем пробелы, ограничиваем длину."""
    if not bug:
        return ""
    s = bug
    # 0) срезаем ведущий "Тип: " / "Тип слово: " (Форма:/Console:/Кнопка «×»:/
    #    Форма скрытое поле:) — в письме жаргон не нужен. Захватываем и
    #    вложенные кавычки «...», и составные типы через пробел.
    s = re.sub(r"^\s*[^:]{1,40}?:\s+", "", s)
    # 0.5) убираем HTML-теги целиком: <type=tel>, <input>, <div ...> и т.п.
    s = re.sub(r"<[^>]+>", " ", s)
    # 1) срезаем CSS-селектор (div.main-card > button.btn-pay) целиком
    s = re.sub(r"[.#]?[a-z][\w-]*(\s*>\s*[.#]?[a-z][\w-]*)+", " ", s, flags=re.I)
    # 2) остатки классов/id-постфиксов: .main-card, #wrap, btn-pay -> убираем
    #    дефисные/точечные хвосты, прилипшие к словам (btn-pay -> btn)
    s = re.sub(r"\.[a-z][\w-]*", " ", s, flags=re.I)
    s = re.sub(r"#[a-z][\w-]*", " ", s, flags=re.I)
    s = re.sub(r"-pay\b|-wrap\b|-box\b|-container\b", " ", s, flags=re.I)
    # 3) тех-термины -> человеческий (порядок в _BUG_HUMAN_RULES важен)
    for pat, repl in _BUG_HUMAN_RULES:
        s = re.sub(pat, repl, s, flags=re.I)
    # 4) чистим мусор: скобки от селекторов, двойные знаки, лишние пробелы
    s = s.replace("(", " ").replace(")", " ").replace("[", " ").replace("]", " ")
    s = s.replace("::", " ").replace("  ", " ")
    # висячие знаки в начале (остались от срезанного селектора/типа): ": ", "- "
    s = re.sub(r"^[\s:;,\-–—>]+", "", s)
    # дубли предлогов после замен ("на mobile" -> "на на телефоне")
    s = re.sub(r"\bна на\b", "на", s, flags=re.I)
    s = re.sub(r"\b(\w+)\s+\1\b", r"\1", s, flags=re.I)  # любое повторённое слово
    # пробел перед знаком препинания
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = " ".join(s.split())
    # первая буква — заглавная (аккуратный вид)
    if s:
        s = s[0].upper() + s[1:]
    # если после чистки почти ничего не осталось — вернём исходник (не теряем смысл)
    if len(s) < 8:
        s = " ".join(bug.split())
    if len(s) > 200:
        s = s[:197].rstrip() + "..."
    return s


def _domain_from_url(url):
    try:
        from urllib.parse import urlparse
        d = urlparse(url or "").netloc or (url or "")
        return d.lower().lstrip("www.")
    except Exception:
        return url or ""


_TEMPLATES_CACHE = None


def _load_templates():
    """Читает letters/templates.txt один раз, кэширует.
    Возвращает список словарей {subject, body} или [] если папки/файла нет."""
    global _TEMPLATES_CACHE
    if _TEMPLATES_CACHE is not None:
        return _TEMPLATES_CACHE
    out = []
    path = os.path.join(LETTERS_DIR, "templates.txt")
    if not os.path.exists(path):
        _TEMPLATES_CACHE = out
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        _TEMPLATES_CACHE = out
        return out
    # блоки === id === ... до следующего === или конца файла.
    # Ведущий блок (до первого ===) — комментарии-заголовок, его split даёт
    # первым элементом; он НЕ содержит SUBJ: -> отфильтруется ниже.
    blocks = re.split(r"^===\s*[\w-]+\s*===\s*$", text, flags=re.M)
    for blk in blocks:
        blk = blk.strip()
        if not blk:
            continue
        lines = blk.splitlines()
        subject = ""
        body_lines = []
        for ln in lines:
            if ln.strip().startswith("#"):
                continue  # строки-комментарии в шаблон не идут
            if ln.startswith("SUBJ:"):
                subject = ln[5:].strip()
            else:
                body_lines.append(ln)
        body = "\n".join(body_lines).strip()
        # ЖЁСТКО: валидный шаблон обязан иметь И тему, И плейсхолдер {bug}
        # в теле. Это отсекает ведущий коммент-блок и любой мусор.
        if subject and body and "{bug}" in body:
            out.append({"subject": subject, "body": body})
    _TEMPLATES_CACHE = out
    return out


def compose_unique_letter(url, notes, tags, recent_hashes=None):
    """Генерирует УНИКАЛЬНОЕ письмо для сайта на основе реального аудита.

    Возвращает (subject, body) или None, если:
      - нет AUDIT:: бага в notes (защита от шаблонного спама: НЕ шлём),
      - нет шаблонов в letters/ (фоллбэк caller'у на letter.txt).

    recent_hashes: множество/список хэшей последних (до 10) отправленных тел.
    Если сгенерированное тело совпадает с одним из них — выбираем ДРУГОЙ
    шаблон, чтобы бот не чередовал 2 шаблона по кругу (А->Б->А->Б).
    """
    bug_raw = extract_audit_bug(notes)
    if not bug_raw:
        return None  # нет бага -> caller НЕ шлёт, сайт остаётся pending
    bug = humanize_bug(bug_raw)
    if not bug:
        bug = bug_raw  # страховка, если humanize срезал всё
    templates = _load_templates()
    if not templates:
        return None
    domain = _domain_from_url(url)
    niche = detect_niche(tags)
    recent = set(recent_hashes or [])

    # Пробуем до 12 шаблонов, пока не найдём уникальный хэш (окно recent).
    tried = 0
    chosen = None
    while tried < min(12, len(templates)):
        tpl = random.choice(templates)
        greeting = random.choice(GREETINGS)
        cta = random.choice(CTA_POOL).replace("{site}", domain)
        try:
            subj = (tpl["subject"] or "Предложение по {site}").format(
                site=domain, bug=bug, niche=niche, greeting=greeting, cta=cta)
            body = tpl["body"].format(
                site=domain, bug=bug, niche=niche, greeting=greeting, cta=cta, tg=TG_HANDLE)
        except Exception:
            tried += 1
            continue
        h = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if h not in recent:
            chosen = (subj, body)
            break
        tried += 1
    if chosen is None:
        # все перебрали и всё в recent (маловероятно) — берём последний рандом
        tpl = random.choice(templates)
        greeting = random.choice(GREETINGS)
        cta = random.choice(CTA_POOL).replace("{site}", domain)
        subj = (tpl["subject"] or "Предложение по {site}").format(
            site=domain, bug=bug, niche=niche, greeting=greeting, cta=cta)
        body = tpl["body"].format(
            site=domain, bug=bug, niche=niche, greeting=greeting, cta=cta, tg=TG_HANDLE)
        chosen = (subj, body)
    return chosen


# Формат строки аудита в notes: AUDIT::<severity>::<type>::<description>[::N]
# Пишется agent_auditor.py через audit_engine.bug_to_note().
_SEVERITY_RANK = {"critical": 3, "high": 3, "medium": 2, "low": 1, "info": 0}


def extract_audit_bug(notes):
    """Извлекает самый серьёзный баг из notes сайта и форматирует его для письма.

    Возвращает короткую человекочитаемую строку (без служебных маркеров/severity)
    или пустую строку "", если багов нет. Никогда не бросает исключение.

    Приоритет: critical/high > medium > low > info. При равенстве — первый по порядку.
    """
    if not notes:
        return ""
    best = None
    best_rank = -1
    for raw in str(notes).splitlines():
        line = raw.strip()
        if not line.startswith("AUDIT::"):
            continue
        parts = line.split("::")
        # parts[0]='AUDIT', [1]=severity, [2]=type, [3]=description, [4]=опц. счётчик
        if len(parts) < 4:
            continue
        severity = (parts[1] or "").strip().lower()
        btype = (parts[2] or "").strip()
        desc = (parts[3] or "").strip()
        if not desc:
            continue
        rank = _SEVERITY_RANK.get(severity, 0)
        if rank > best_rank:
            best_rank = rank
            # Тип полезен как контекст ("Форма", "Console"), но не обязателен.
            if btype and btype.lower() not in desc.lower():
                best = f"{btype}: {desc}"
            else:
                best = desc
    if not best:
        return ""
    # Обрезаем слишком длинные описания — письмо должно быть коротким.
    best = " ".join(best.split())  # схлопываем переводы строк/пробелы
    if len(best) > 220:
        best = best[:217].rstrip() + "..."
    return best
