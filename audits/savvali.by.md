# Отчёт по тестированию сайта savvali.by

**Дата:** 11.07.2026
**Проверял:** Назар (ручное тестирование)
**Платформа сайта:** Tilda / самопис / WordPress
**Контакт владельца:** savvali.by@gmail.com

---

## 1. Что проверяли
- Адаптивность (телефон + компьютер)
- Формы: регистрация, заказ, обратная связь
- Логические баги и недочёты интерфейса
- Скорость загрузки, SEO, поведение в разных браузерах

---

## 2. Найденные баги
| # | Где | Описание | Скриншот | Критичность |
|---|-----|----------|----------|-------------|
| 1 | Console / DevTools | Ошибка в консоли браузера: [warning] cdn.tailwindcss.com should not be used in production. To use Tailwind CSS in production, install it as a PostCSS plugin or use the Tailwind CLI: https | — | MEDIUM |
| 2 | Console / DevTools | Ошибка в консоли браузера: [warning] Swiper Loop Warning: The number of slides is not enough for loop mode, it will be disabled and not function properly. You need to add more slides (or  | — | MEDIUM |
| 3 | Console / DevTools | Ошибка в консоли браузера: [warning] [Meta Pixel] - You are sending a non-standard event 'Mainpage_krokit'. The preferred way to send these events is using trackCustom. See 'https://devel | — | MEDIUM |

> Если багов нет — пишем честно:
> **«Критических и функциональных багов не найдено. Сайт работает корректно.»**
> (НЕ придумывать баги — владелец сразу поймёт обман.)

---

## 3. Рекомендации по улучшению
Даже на чистом сайте есть места, где можно увеличить конверсию и удержание посетителей:

- **SEO:** заполнены ли title / description, скорость загрузки, есть ли микроразметка (schema.org)
- **UX:** понятен ли путь заказа, есть ли чёткий CTA (призыв к действию), не перегружены ли страницы
- **Адаптив:** как сайт выглядит на реальном телефоне (iPhone / Android), а не только наличие тега viewport
- **Разные браузеры:** Safari / Firefox vs Chrome — одинаково ли отображается
- **Edge cases:** пустая корзина, очень длинное имя, невалидный email, повторная регистрация, отправка формы с пустыми полями

---

## 4. Итог
Найдено 3 багов. Топ-приоритет: Console / DevTools; Console / DevTools; Console / DevTools

---
*Проверено в рамках бесплатного аудита. Готов обсудить доработки или регулярную проверку сайта.*


## 5. Авто-аудит (Гордон + agent-browser)

Найдено 3 багов. Топ-приоритет: Console / DevTools; Console / DevTools; Console / DevTools

- Всего багов: 3
- 1. **[MEDIUM]** Console / DevTools: Ошибка в консоли браузера: [warning] cdn.tailwindcss.com should not be used in production. To use Tailwind CSS in production, install it as a PostCSS plugin or use the Tailwind CLI: https
- 2. **[MEDIUM]** Console / DevTools: Ошибка в консоли браузера: [warning] Swiper Loop Warning: The number of slides is not enough for loop mode, it will be disabled and not function properly. You need to add more slides (or 
- 3. **[MEDIUM]** Console / DevTools: Ошибка в консоли браузера: [warning] [Meta Pixel] - You are sending a non-standard event 'Mainpage_krokit'. The preferred way to send these events is using trackCustom. See 'https://devel
