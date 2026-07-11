# Развёртывание Гордона на бесплатном стеке (без карты)

Цель: Гордон крутится 24/7 на GitHub Actions, бот отвечает из любой точки мира через
Cloudflare Worker, `/ask` бесплатно через OpenRouter. Всё $0, карта не нужна
(белорусская карта заблокирована - поэтому этот стек, а не обычный VPS).

## Что уже готово в папке outreach-tracker
- `.github/workflows/gordon.yml` - запускает Гордона каждый час, коммитит базу+лог.
- `gordon_status.py` - эмитит `status.json` (статус без паролей).
- `gordon_bot_worker.js` - код Telegram-бота для Cloudflare Worker.
- `.env.secrets.example` - шаблон секрета DOTENV.

## Шаг 1. GitHub-репозиторий
1. Зайди на github.com (регистрация бесплатна, карта не нужна).
2. Создай НОВЫЙ репозиторий (public). Имя, напр. `outreach-tracker`.
3. На ПК в папке outreach-tracker:
   ```
   git init
   git add .
   git commit -m "gordon free stack"
   git branch -M main
   git remote add origin https://github.com/<ТВОЙ_ЛОГИН>/outreach-tracker.git
   git push -u origin main
   ```
   (`.env` и `outreach.db` уже в `.gitignore` - не улетят. Проверь: `git ls-files | grep -E "\.env$|outreach\.db"` должно быть пусто.)

## Шаг 2. Секрет DOTENV
1. В репо: Settings -> Secrets and variables -> Actions -> New repository secret.
2. Name: `DOTENV`
3. Secret: вставь СОДЕРЖИМОЕ своего локального файла `.env` целиком (с реальными
   Gmail-паролями), одной простынёй. Берётся из `.env.secrets.example` как шаблон.
4. Add secret. GitHub маскирует его в логах.

## Шаг 3. Первый прогон
1. Actions -> Gordon Outreach -> Run workflow (или подожди ближайший час по cron).
2. Через ~30 мин глянь: должен появиться зелёный прогон и файл `status.json` в репо.
3. ВАЖНО: проверь, что письма ушли без баунса (GitHub IP может спровоцировать Gmail
   "new sign-in" - App Password это обходит, но первый раз глянь лог).

## Шаг 4. Cloudflare Worker (бот)
1. Зайди на dash.cloudflare.com (бесплатно, карта не нужна).
2. Workers & Pages -> Create -> Create Worker. Имя любое, напр. `gordon-bot`.
3. Замени код воркера на содержимое `gordon_bot_worker.js`.
4. Settings -> Variables -> Secrets: добавь 4 секрета:
   - `TG_BOT_TOKEN` - (шаг 5)
   - `ALLOWED_CHAT` - (шаг 6)
   - `OPENROUTER_API_KEY` - (шаг 7)
   - `STATUS_URL` - `https://raw.githubusercontent.com/<ЛОГИН>/outreach-tracker/main/status.json`
5. Deploy. Запомни URL воркера (типа `https://gordon-bot.<твой>.workers.dev`).

## Шаг 5. Бот в @BotFather
1. В Telegram напиши @BotFather, `/newbot`, имя + юзернейм (напр. `GordonNazarBot`).
2. Получишь токен вида `123456:ABC-DEF...`. Скопируй.
3. Впиши его в секрет `TG_BOT_TOKEN` воркера (шаг 4.4).

## Шаг 6. Свой chat_id
1. Напиши что-нибудь своему боту в Telegram.
2. Открой в браузере `https://gordon-bot.<твой>.workers.dev/` (health check) -
   нет, chat_id так не видно. Проще: написать @userinfobot в Telegram -> он вернёт
   твой chat_id. Скопируй число в секрет `ALLOWED_CHAT`.
3. (Пока ALLOWED_CHAT пуст - бот отвечает ВСЕМ. Задай сразу после теста.)

## Шаг 7. OpenRouter (для /ask)
1. Зайди на openrouter.ai, зарегайся (бесплатно).
2. Keys -> Create Key. Скопируй.
3. Впиши в секрет `OPENROUTER_API_KEY` воркера.
4. Free-модель в коде уже прописана (`deepseek/deepseek-chat-v3-0324:free`).

## Шаг 8. Прописать webhook
Открой в браузере:
`https://gordon-bot.<твой>.workers.dev>/setwebhook`
Должно вернуть `{"ok":true,...}`. Теперь Telegram шлёт сообщения воркеру.

## Шаг 9. ОТКЛЮЧИТЬ Windows Task Scheduler
Чтобы не было двойной отправки (ПК + Actions):
1. На ПК: Панель управления -> Администрирование -> Планировщик заданий.
2. Найди `GordonOutreach` -> Отключить (или Удалить).
Гордон теперь только в GitHub Actions (24/7).

## Проверка
- Напиши боту `/status` с телефона (вне ПК) -> видишь сводку Гордона.
- `/log 5` -> последние 5 строк лога.
- `/ask что делает Гордон` -> бесплатный ответ модели.
- Чужой chat_id -> бот молчит (ALLOWED_CHAT фильтр).

## Правила синхронизации БД (один писатель за раз)
Гордон крутится только в GitHub Actions (writer). ПК = редактор. Чтобы не было
битых конфликтов при git-синхронизации используется `journal_mode=DELETE`
(весь журнал в одном файле `outreach.db`, без `-wal`/`-shm`), и Actions делает
`git pull --rebase --autostash` перед прогоном + `wal_checkpoint(TRUNCATE)`
перед коммитом (всё уже в `gordon.yml`).

Локально есть `sync.py`:
- `python sync.py pull` - забрать свежую БД/логи из репо (rebase + autostash).
- `python sync.py push` - закоммитить свои правки БД/логов и запушить.

Порядок работы на ПК:
1. `python sync.py pull` ДО любой правки сайтов (иначе перезапишешь данные Actions).
2. Правь через `track.py` / `gordon_desktop.py`.
3. `python sync.py push` ПОСЛЕ правки.
4. Планировщик GordonOutreach на ПК ОТКЛЮЧЁН (шаг 9) - иначе двойная отправка.

## Если сломалось
- Бот не отвечает: глянь логи воркера (Workers -> твой -> Logs), проверь секреты.
- status.json пустой/старый: глянь GitHub Actions - был ли зелёный прогон.
- Письма не уходят: глянь `gordon_run.log` в репо на баунсы/ошибки логина.
- Двойная отправка: проверь, что ПК-планировщик отключён (шаг 9).
- Конфликт БД (ПК и Actions правили одновременно): `python sync.py pull` сделает
  rebase; если правка Actions важнее - `git checkout origin/main -- outreach.db`
  и заново `python sync.py pull`.
