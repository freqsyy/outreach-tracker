# lane01 SCOUT - test-drive score-колонки (scout-2026-07-14-16)

- Дата: 2026-07-14
- Файл: `outreach-tracker/track.py`
- Режим: DRY (в БД НЕ писалось)
- Формула: `score = 0.30*YOUNG + 0.25*REACH + 0.25*BUGGY + 0.10*NICHE + 0.10*FRESH` (scout_filters_v2)

## Команды
```
python track.py score <domain>            # dry, без фетча (buggy=0, возраст из RDAP не брался)
python track.py score <domain> --fetch    # +live RDAP (age) + GET сайта (buggy/contact), SSRF-гард
python track.py list --by-score           # сортировка по score; пустые score считаются on-the-fly (*)
```

## Test-drive на 3 доменах (DRY, без --fetch)

### 1. allergyspotter.com
```
DOMAIN            SCORE YOUNG REACH BUGGY NICHE FRESH
allergyspotter.com 43   40    75    0     25    95
SIGNALS: email(1), tg
WHY: YOUNG 40*.30 + REACH 75*.25 + BUGGY 0*.25 + NICHE 25*.10 + FRESH 95*.10
```
Профиль: почта есть (не на домене -> REACH 75), возраст не брался (dry),
категория пуста (NICHE 25), свежий дамп (FRESH 95).

### 2. builtatnight.dev
```
DOMAIN         SCORE YOUNG REACH BUGGY NICHE FRESH
builtatnight.dev 49   40    100   0     25    95
SIGNALS: email(1), tg
WHY: YOUNG 40*.30 + REACH 100*.25 + BUGGY 0*.25 + NICHE 25*.10 + FRESH 95*.10
```
Профиль: почта НА домене (hello@builtatnight.dev -> REACH 100).

### 3. diffbundle.com
```
DOMAIN        SCORE YOUNG REACH BUGGY NICHE FRESH
diffbundle.com 43   40    75    0     25    95
SIGNALS: email(1), tg
WHY: YOUNG 40*.30 + REACH 75*.25 + BUGGY 0*.25 + NICHE 25*.10 + FRESH 95*.10
```
Профиль: почта на чужом домене (support@interhive.org -> REACH 75).

## Test-drive с --fetch (live сигналы) на builtatnight.dev
```
builtatnight.dev 67   100   100   0     25    95
SIGNALS: age=4d, email(2), tg
AGE_DAYS: 4
```
-> Возраст через RDAP = 4 дня => YOUNG поднялся 40 -> 100, score 49 -> 67.
Багги-маркеры (buggy=0): HTML прошёл базовые проверки (есть viewport, нет
lorem/TODO). Фетч read-only через SSRF-гард `url_is_safe()`.

## list --by-score (top 8)
```
ID   Status     Score  URL                         Email                  Telegram
289  ? review   49*    https://sonatly.com         hello@sonatly.com      @sonatly
291  ? review   49*    https://bereanmind.com      support@bereanmind.com @bereanmind @keyfra
...
```
-> Сортировка по score DESC; пустые score в БД (нет `cat:`, dry) считаются
on-the-fly и помечаются `*` (НЕ записаны).

## Проверка целостности БД
- Всего строк: 323
- Строк с непустым/ненулевым score: 0
- => **БД НЕ изменена** dry-run'ом (constraint соблюдён).

## SSRF-гарды (в силе)
- `_fetch_text` обёрнут в `url_is_safe()` (scheme/userinfo/IP/loopback/
  private/link-local/IMDS блокируются).
- Все внешние запросы (RDAP/HTML) идут ТОЛЬКО через гард.
- Никакой записи/апдейта БД в `score`/`cmd_score`.

## Что финализировано (vs scout-2026-07-14-12)
- `score_lead(row)` - 0-100 по 5 сигналам (YOUNG/REACH/BUGGY/NICHE/FRESH).
- `track.py score --domain X` - таблица (domain, сигналы, score), dry.
- `track.py score --domain X --fetch` - +live RDAP/HTML (read-only, SSRF).
- `track.py list --by-score` - колонка score; on-the-fly если в БД пусто.
- `migrate_score_column()` - идемпотентно добавляет колонку score.
- `edit --score` - единственный путь записи score (ждёт аппрув Назара).
- Починен баг cmd_export (KeyError 'bounced'/'review').
