# Карта подводных камней Гордона (по методу Назара)

Здесь агенты сами фиксируют грабли при работе. Перед запуском — проверь.

## Перед стартом (чек-лист)
- [ ] `.env` заполнен (минимум 1 ACCOUNT_x_EMAIL + APP_PASSWORD)
- [ ] `gordon_sources.txt` содержит URL (по одному на строку)
- [ ] `track.py` инициализирован (`python track.py stats` работает)
- [ ] Gmail аккаунты: включён менее безопасный доступ / создан App Password

## Sender: pusto v .env
- Ошибка: agenty ne mogut otpravit pisma
- Причина: net zaponennyh ACCOUNT_x v .env
- Решение: zapolnit .env po .env.example, dobavit spare-gmail akkaunty
- Замечено: 2026-07-09 21:01

## Sender: oshibka SMTP
- Ошибка: (535, b'5.7.8 Username and Password not accepted. For more information, go to\n5.7.8  https://support.google.com/mail/?p=BadCredentials 5b1f17b1804b1-493f2a2e499sm8284215e9.0 - gsmtp')
- Причина: blok/nekorrektnyy parol/limit akkaunta
- Решение: proverit APP_PASSWORD, rotirovat akkaunt, umenshit MAX_PER_DAY
- Замечено: 2026-07-09 21:44
