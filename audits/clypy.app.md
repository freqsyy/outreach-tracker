# Отчёт по тестированию сайта clypy.app

**Дата:** 18.07.2026
**Проверял:** Назар (ручное тестирование)
**Платформа сайта:** Tilda / самопис / WordPress
**Контакт владельца:** support@clypy.app

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
| 1 | Console / DevTools | Ошибка в консоли браузера: [warning] You are running production build of Inferno in development mode. Use dev:module entry point. | — | MEDIUM |
| 2 | Console / DevTools | Ошибка в консоли браузера: [warning] Warning: cart block is not added to this page | — | MEDIUM |
| 3 | Console / DevTools | Ошибка в консоли браузера: [warning] Google Maps JavaScript API has been loaded directly without loading=async. This can result in suboptimal performance. For best-practice loading patter | — | MEDIUM |
| 4 | Console / DevTools | Ошибка в консоли браузера: [warning] JQMIGRATE: jQuery.fn.bind() is deprecated | — | MEDIUM |
| 5 | Console / DevTools | Ошибка в консоли браузера: [warning] JQMIGRATE: jQuery.unique is deprecated, use jQuery.uniqueSort | — | MEDIUM |

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
Найдено 5 багов. Топ-приоритет: Console / DevTools; Console / DevTools; Console / DevTools

---
*Проверено в рамках бесплатного аудита. Готов обсудить доработки или регулярную проверку сайта.*


## 5. Авто-аудит (Гордон + agent-browser)

Найдено 5 багов. Топ-приоритет: Console / DevTools; Console / DevTools; Console / DevTools

- Всего багов: 5
- 1. **[MEDIUM]** Console / DevTools: Ошибка в консоли браузера: [warning] You are running production build of Inferno in development mode. Use dev:module entry point.
- 2. **[MEDIUM]** Console / DevTools: Ошибка в консоли браузера: [warning] Warning: cart block is not added to this page
- 3. **[MEDIUM]** Console / DevTools: Ошибка в консоли браузера: [warning] Google Maps JavaScript API has been loaded directly without loading=async. This can result in suboptimal performance. For best-practice loading patter
- 4. **[MEDIUM]** Console / DevTools: Ошибка в консоли браузера: [warning] JQMIGRATE: jQuery.fn.bind() is deprecated
- 5. **[MEDIUM]** Console / DevTools: Ошибка в консоли браузера: [warning] JQMIGRATE: jQuery.unique is deprecated, use jQuery.uniqueSort
