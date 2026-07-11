#!/usr/bin/env python3
"""
audit_engine.py — ЯДРО аудита сайта через Chromium (agent-browser + CDP).

Один источник правды для:
  - agent_auditor.py  (агент Гордона: прогоняет pending-сайты)
  - audit_site.py     (standalone-услуга «ИИ-аудит сайта»)

Что проверяет (базовый QA-чекап, как в Миссиях 4/8):
  1. Скрытые required-поля  -> блокируют отправку формы (главный баг, как на freelancespace)
  2. Console-ошибки         -> JS-падения
  3. Сетевые 4xx/5xx        -> битые ресурсы/роуты на домене сайта
  4. Мобильный overflow     -> горизонтальный скролл на 375px
  5. Перекрытые кнопки      -> клик «проваливается» (covered by)

Только стандартная библиотека (subprocess). Chrome должен быть уже поднят
на CDP 9222 (см. инструкцию в raw/2026-07-11-agent-browser-tested.md).
Если порт свободен — движок НЕ спавнит Chrome сам (без ведома Назара),
а возвращает warning в списке багов.

Запуск Chrome (вручную, один раз):
  CHROME="C:/Users/nazar/.agent-browser/browsers/chrome-150.0.7871.115/chrome.exe"
  "$CHROME" --remote-debugging-port=9222 --headless=new --no-first-run \
    --user-data-dir=/tmp/ab-chrome-profile about:blank
"""

import subprocess
import re
import json
import os
import time
from urllib.parse import urlparse

CDP_PORT = "9222"
# На Windows `subprocess` не резолвит .cmd без расширения -> нужен явный .cmd
AB = "agent-browser"
try:
    subprocess.run([AB, "--version"], capture_output=True, text=True, timeout=10)
except FileNotFoundError:
    AB = "agent-browser.cmd"


def _run(args, timeout=60):
    """Обёртка над agent-browser. Возвращает stdout (str) или '' при ошибке."""
    try:
        r = subprocess.run(
            [AB, "--cdp", CDP_PORT] + args,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


def cdp_alive():
    """Хром считаем живым, если порт 9222 отвечает вообще (даже если открыта
    страница-ошибка chrome-error://). Проверяем сам порт, а не содержимое URL —
    иначе живой Хром на chrome-error:// ложно считался мёртвым и аудит сыпал мусор."""
    try:
        import socket
        with socket.create_connection(("127.0.0.1", int(CDP_PORT)), timeout=2):
            return True
    except Exception:
        pass
    # запасной вариант — если порт открыт, но socket не сработал, спросим браузер
    out = _run(["get", "url"], timeout=10)
    return bool(out.strip())


# --- Авто-подъём Chromium (чтобы прогон не обрывался, если порт умер) ---
CHROME_BIN = None
def _find_chrome():
    """Ищет бинарь Chrome for Testing (CFT), с которым работает agent-browser.
    Возвращает путь или None."""
    global CHROME_BIN
    if CHROME_BIN and os.path.exists(CHROME_BIN):
        return CHROME_BIN
    base = os.path.expanduser("~/.agent-browser/browsers")
    if not os.path.isdir(base):
        return None
    # ищем первый каталог chrome-* внутри
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name, "chrome.exe")
        if os.path.exists(p):
            CHROME_BIN = p
            return p
    return None


def launch_chrome():
    """Поднимает Chromium на CDP 9222 в фоне. Возвращает True если порт ожил.
    Если бинарь не найден или порт уже занят другим процессом — False."""
    binp = _find_chrome()
    if not binp:
        return False
    try:
        # detached + CREATE_NEW_PROCESS_GROUP: процесс живёт независимо от нас
        subprocess.Popen(
            [binp, "--remote-debugging-port=9222", "--headless=new",
             "--no-first-run", "--no-default-browser-check",
             f"--user-data-dir={os.path.expanduser('~/.ab-chrome-profile')}",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except Exception:
        return False
    # ждём появления порта (до ~15с)
    for _ in range(15):
        if cdp_alive():
            return True
        time.sleep(1)
    return False


def ensure_cdp(max_retries=2):
    """Гарантирует живой CDP перед аудитом сайта.
    Если порт мёртв — пытается поднять Chromium, retry раз.
    Возвращает True/False. НЕ пишет никакого мусора — вызывающий сам решает,
    что делать при False (пропустить сайт, не трогать notes)."""
    if cdp_alive():
        return True
    for _ in range(max_retries):
        if launch_chrome():
            return True
        time.sleep(2)
    return False




# ---------- низкоуровневые хелперы ----------

def open_page(url):
    return _run(["open", url], timeout=60)


def eval_js(js, timeout=30):
    return _run(["eval", js], timeout=timeout)


def _parse_json(out, default=None):
    """agent-browser eval может вернуть строку-в-строке (JSON внутри JSON-строки)
    либо '✗ ...' при ошибке. Двойной парс + защита."""
    if not out or out.lstrip().startswith("✗"):
        return default
    s = out.strip()
    try:
        v = json.loads(s)
    except Exception:
        return default
    # если получили строку, содержащую JSON — парсим ещё раз
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return default
    return v


def console_errors():
    out = _run(["console"], timeout=30)
    # оставляем только строки с error/warning
    lines = [l for l in out.splitlines() if re.search(r"\b(error|warning)\b", l, re.I)]
    return lines


def network_bad(domain):
    """Возвращает список строк-запросов с 4xx/5xx на домене сайта (не аналитика)."""
    out = _run(["network", "requests", "--filter", domain], timeout=40)
    bad = []
    for line in out.splitlines():
        m = re.search(r"\((Document|Stylesheet|Script|Image|XHR|Fetch|Ping)\)\s+(\d{3})", line)
        if not m:
            continue
        status = int(m.group(2))
        if status >= 400 and domain in line:
            bad.append(line.strip())
    return bad


def set_viewport(w, h):
    _run(["set", "viewport", str(w), str(h)], timeout=20)


# ---------- детекторы багов ----------

def _detect_hidden_required():
    """Ищет [required] поля, скрытые от юзера (offsetParent===null).
    Возвращает список словарей багов."""
    js = (
        "JSON.stringify("
        "[].slice.call(document.querySelectorAll('[required]'))"
        ".map(function(el){return {tag: el.tagName, type: el.type, name: el.name, id: el.id, "
        "visible: el.offsetParent !== null, msg: el.validationMessage};})"
        ".filter(function(x){return !x.visible;})"
        ")"
    )
    out = eval_js(js)
    hidden = _parse_json(out, default=[])
    if not isinstance(hidden, list):
        return []
    bugs = []
    for h in hidden:
        bugs.append({
            "severity": "critical",
            "where": "форма (скрытое поле)",
            "desc": (f"Скрытое обязательное поле <{h.get('tag','INPUT').lower()}"
                     f"{('#'+h['id']) if h.get('id') else ''} type={h.get('type','')}> "
                     f"с атрибутом required='true', но невидимо пользователю. "
                     f"Сообщение браузера: «{h.get('msg','')}». "
                     f"Форма не отправляется, а ошибка юзеру не показывается."),
            "evidence": f"validationMessage={h.get('msg','')}",
        })
    return bugs


def _detect_covered_submits():
    """Кнопки submit/Создать, перекрытые другим элементом (клик проваливается)."""
    js = (
        "JSON.stringify("
        "[].slice.call(document.querySelectorAll('button, input[type=submit], a.btn, [role=button]'))"
        ".filter(function(b){"
        "  var s = getComputedStyle(b);"
        "  return b.offsetParent !== null && s.visibility !== 'hidden' && s.display !== 'none'"
        "    && !/notranslate|goog-/.test(b.className);"
        "})"
        ".map(function(b){"
        "  var r = b.getBoundingClientRect();"
        "  if (r.width === 0 || r.height === 0) return null;"
        "  var cx = r.x + r.width/2, cy = r.y + r.height/2;"
        "  var top = document.elementFromPoint(cx, cy);"
        "  if (!top) return null;"
        "  var tc = String(top.className);"
        "  if (/notranslate|goog-/.test(tc)) return null;"
        "  var covered = top !== b && !b.contains(top) && !top.contains(b);"
        "  return covered ? {txt: (b.textContent||b.value||'').trim().slice(0,30), "
        "                    cls: top.tagName+'.'+String(top.className).slice(0,40)} : null;"
        "})"
        ".filter(function(x){return x;})"
        ")"
    )
    out = eval_js(js)
    covered = _parse_json(out, default=[])
    if not isinstance(covered, list):
        return []
    bugs = []
    for c in covered:
        bugs.append({
            "severity": "high",
            "where": f"кнопка «{c.get('txt','')}»",
            "desc": (f"Кнопка перекрыта другим элементом ({c.get('cls','')}) — "
                     f"клик по ней визуально не срабатывает, попадает на перекрывающий блок."),
            "evidence": f"elementFromPoint -> {c.get('cls','')}",
        })
    return bugs


def _detect_mobile_overflow():
    set_viewport(375, 812)
    out = eval_js(
        "JSON.stringify({sw: document.documentElement.scrollWidth, "
        "cw: document.documentElement.clientWidth})"
    )
    set_viewport(1280, 800)
    d = _parse_json(out, default={})
    if not isinstance(d, dict):
        return []
    if d.get("sw", 0) > d.get("cw", 0) + 1:
        return [{
            "severity": "medium",
            "where": "мобильная вёрстка (375px)",
            "desc": (f"Горизонтальный переполнение на телефоне: scrollWidth={d['sw']} > "
                     f"clientWidth={d['cw']} (на {d['sw']-d['cw']}px). Контент уезжает за экран."),
            "evidence": f"scrollW={d['sw']} clientW={d['cw']}",
        }]
    return []


def _detect_console():
    errs = console_errors()
    bugs = []
    for e in errs[:5]:  # не более 5, чтобы не забивать отчёт
        bugs.append({
            "severity": "medium",
            "where": "Console / DevTools",
            "desc": f"Ошибка в консоли браузера: {e.strip()[:160]}",
            "evidence": e.strip()[:160],
        })
    return bugs


def _detect_network(domain):
    bad = network_bad(domain)
    bugs = []
    for b in bad[:5]:
        m = re.search(r"\((.*?)\)\s+(\d{3})", b)
        st = m.group(2) if m else "?"
        res = m.group(1) if m else "?"
        bugs.append({
            "severity": "high" if int(st) >= 500 else "medium",
            "where": "Network (ресурс сайта)",
            "desc": f"Запрос вернул {st} ({res}) на домене сайта. Битый ресурс/роут.",
            "evidence": b.strip()[:160],
        })
    return bugs


# ---------- формы: ищем страницы логина/регистрации и чекаем ----------

def _find_auth_pages():
    js = (
        "JSON.stringify("
        "[].slice.call(document.querySelectorAll('a[href]'))"
        ".map(function(a){return a.getAttribute('href');})"
        ".filter(function(h){return /(register|signup|sign-up|login|signin|auth|войти|регистр)/i.test(h);})"
        ".map(function(h){return h.indexOf('http')===0 ? h : location.origin + '/' + h.replace(/^\\.?\\//,'');})"
        ".slice(0, 4)"
        ")"
    )
    out = eval_js(js)
    return _parse_json(out, default=[]) or []


# ---------- публичный API ----------

def audit_url(url, check_forms=True):
    """Прогоняет базовый чекап URL. Возвращает dict:
    { domain, url, bugs:[...], summary, has_critical }"""
    domain = urlparse(url).netloc or url
    bugs = []

    # гарантируем живой CDP ПЕРЕД каждым сайтом (Хром мог умереть посреди прогона)
    if not ensure_cdp():
        # НЕ возвращаем «баг» — это служебная инфа, в письмо клиенту она не пойдёт.
        # Пустой bugs => bug_to_note("") => письмо уходит ОБЫЧНЫМ, без блока бага.
        return {
            "domain": domain, "url": url,
            "bugs": [],
            "summary": "Chromium не запущен (CDP 9222 закрыт) — аудит пропущен.",
            "has_critical": False,
            "skipped": True,
        }

    open_page(url)
    # повторная проверка: Хром мог отвалиться ровно между ensure и open_page
    if not cdp_alive():
        return {
            "domain": domain, "url": url,
            "bugs": [],
            "summary": "Chromium отвалился в процессе — аудит пропущен.",
            "has_critical": False,
            "skipped": True,
        }

    bugs += _detect_hidden_required()
    if bugs:  # стоп сразу после первого реального бага — для письма нужен один
        return _finalize(url, domain, bugs)
    bugs += _detect_covered_submits()
    if bugs:
        return _finalize(url, domain, bugs)
    bugs += _detect_mobile_overflow()
    if bugs:
        return _finalize(url, domain, bugs)
    bugs += _detect_console()
    if bugs:
        return _finalize(url, domain, bugs)
    bugs += _detect_network(domain)
    if bugs:
        return _finalize(url, domain, bugs)

    if check_forms:
        for auth_url in _find_auth_pages():
            open_page(auth_url)
            bugs += _detect_hidden_required()
            bugs += _detect_covered_submits()

    return _finalize(url, domain, bugs)


def _finalize(url, domain, bugs):
    """Дедуп + сортировка + сборка результата (общий финал для раннего
    выхода и для полного прогона)."""
    # дедуп по (where, desc) — одна и та же проблема не должна дублироваться
    seen = set()
    uniq = []
    for b in bugs:
        key = (b["where"], b["desc"][:80])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(b)
    bugs = uniq

    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    bugs.sort(key=lambda b: sev_rank.get(b["severity"], 9))
    has_critical = any(b["severity"] == "critical" for b in bugs)

    if bugs:
        summary = f"Найдено багов: {len(bugs)}. " + "; ".join(
            f"{b['severity']}: {b['where']}" for b in bugs[:3]
        )
    else:
        summary = "Критических и функциональных багов не найдено. Сайт работает корректно."

    return {
        "domain": domain, "url": url,
        "bugs": bugs, "summary": summary, "has_critical": has_critical,
    }


_NOISE_WHERE = ("console / devtools", "console", "devtools")
def _is_noise(b):
    """Шумный баг — не годится как главный в письмо/бартер.
    Это pure-warning консоли без реальной ошибки (чаще всего tailwind-CDN
    в проде, либо общее «warning»). Владельца НЕ впечатлит, выглядит как спам."""
    if b["where"].lower() in _NOISE_WHERE:
        d = b["desc"].lower()
        if "warning" in d or "tailwind" in d:
            return True
    return False


def bug_to_note(result):
    """Краткая строка бага для notes в БД (маркер AUDIT:: для парсинга).
    Формат: AUDIT::<sev>::<where>::<desc>::<total_count>
    total_count — сколько всего багов нашли (для счётчика «и ещё N» в письме).
    Без обрезки desc — чтобы баг не обрывался посреди слова в письме/бартере.
    Топ-баг — первый НЕ-шумный (critical/high/реальный medium); если все баги
    шумные — пишем без бага (честно, без «warning tailwind» в письме)."""
    if not result["bugs"]:
        return ""
    real = [b for b in result["bugs"] if not _is_noise(b)]
    top = real[0] if real else None
    total = len(result["bugs"])
    if not top:
        # все баги — шум: в письмо/бартер ничего не пишем, счётчик по ним не считаем
        return ""
    return f"AUDIT::{top['severity']}::{top['where']}::{top['desc']}::{total}"


def bug_to_letter_line(result):
    """Строка бага для подстановки в письмо (плейсхолдер {bug}). Пусто если багов нет."""
    if not result["bugs"]:
        return ""
    top = result["bugs"][0]
    return f"Пока изучал сайт, нашёл баг: {top['desc']}"


if __name__ == "__main__":
    import sys
    test = sys.argv[1] if len(sys.argv) > 1 else "https://freelancespace.ru"
    r = audit_url(test)
    print(json.dumps(r, ensure_ascii=False, indent=2))
