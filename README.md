# Гордон — армия агентов для аутрича (v0.2)

Центр управления тремя агентами поверх `track.py` (SQLite).

## Агенты
1. **agent_parser.py** — парсит URL из `gordon_sources.txt`, вытаскивает email+telegram (curl), заливает в БД через `track.py add`.
2. **agent_sender.py** — берёт `pending` с email, ротирует spare-аккаунты из `.env`, шлёт письмо через SMTP, ставит `send`.
3. **agent_recorder.py** — читает `gordon_responses.txt` (ручной ввод результатов) и обновляет статусы (`reply`/`hired`/`rejected` + сумма).

**gordon.py** — командир: гоняет всех троих по очереди, пишет `gordon_run.log`.

## Быстрый старт
```bash
cd C:\Users\nazar\outreach-tracker
cp .env.example .env        # заполни аккаунты + app-password
# добавь URL в gordon_sources.txt
python gordon.py            # один прогон всех агентов
python track.py stats       # проверить результат
```

## Безопасность
- Лимиты в `.env`: `MAX_PER_RUN` / `MAX_PER_DAY` / `SEND_INTERVAL_SEC` / `HOURS` — чтобы Gmail не забанил за массовку.
- `.env` в `.gitignore` — секреты не уходят в git.
- IMAP-поллинг выключен по умолчанию (`IMAP_ENABLED=false`).

## Грабли
См. `gordon_pitfalls.md` — агенты сами дописывают туда ошибки.
