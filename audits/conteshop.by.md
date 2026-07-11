# Отчёт по тестированию сайта conteshop.by

**Дата:** 11.07.2026
**Проверял:** Назар (ручное тестирование)
**Платформа сайта:** Tilda / самопис / WordPress
**Контакт владельца:** smm@conte.by

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
| 1 | форма (скрытое поле) | Скрытое обязательное поле <input#delaweb-sbs-email type=email> с атрибутом required='true', но невидимо пользователю. Сообщение браузера: «Заполните это поле.». Форма не отправляется, а ошибка юзеру не показывается. | — | CRITICAL |
| 2 | форма (скрытое поле) | Скрытое обязательное поле <input#youama-subscribe-side-popup type=checkbox> с атрибутом required='true', но невидимо пользователю. Сообщение браузера: «Чтобы продолжить, установите этот флажок.». Форма не отправляется, а ошибка юзеру не показывается. | — | CRITICAL |
| 3 | кнопка «Найти» | Кнопка перекрыта другим элементом (DIV.modal-up__background-close) — клик по ней визуально не срабатывает, попадает на перекрывающий блок. | — | HIGH |
| 4 | кнопка «Принять» | Кнопка перекрыта другим элементом (DIV.modal-up__background-close) — клик по ней визуально не срабатывает, попадает на перекрывающий блок. | — | HIGH |
| 5 | кнопка «Отклонить» | Кнопка перекрыта другим элементом (DIV.modal-up__background-close) — клик по ней визуально не срабатывает, попадает на перекрывающий блок. | — | HIGH |
| 6 | кнопка «Настроить» | Кнопка перекрыта другим элементом (DIV.modal-up__background-close) — клик по ней визуально не срабатывает, попадает на перекрывающий блок. | — | HIGH |
| 7 | Console / DevTools | Ошибка в консоли браузера: [warning] cdn.tailwindcss.com should not be used in production. To use Tailwind CSS in production, install it as a PostCSS plugin or use the Tailwind CLI: https | — | MEDIUM |
| 8 | Console / DevTools | Ошибка в консоли браузера: [warning] Swiper Loop Warning: The number of slides is not enough for loop mode, it will be disabled and not function properly. You need to add more slides (or  | — | MEDIUM |
| 9 | Console / DevTools | Ошибка в консоли браузера: [warning] [Meta Pixel] - You are sending a non-standard event 'Mainpage_krokit'. The preferred way to send these events is using trackCustom. See 'https://devel | — | MEDIUM |

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
Найдено 9 багов. Топ-приоритет: форма (скрытое поле); форма (скрытое поле); кнопка «Найти»

---
*Проверено в рамках бесплатного аудита. Готов обсудить доработки или регулярную проверку сайта.*


## 5. Авто-аудит (Гордон + agent-browser)

Найдено 9 багов. Топ-приоритет: форма (скрытое поле); форма (скрытое поле); кнопка «Найти»

- Всего багов: 9
- 1. **[CRITICAL]** форма (скрытое поле): Скрытое обязательное поле <input#delaweb-sbs-email type=email> с атрибутом required='true', но невидимо пользователю. Сообщение браузера: «Заполните это поле.». Форма не отправляется, а ошибка юзеру не показывается.
- 2. **[CRITICAL]** форма (скрытое поле): Скрытое обязательное поле <input#youama-subscribe-side-popup type=checkbox> с атрибутом required='true', но невидимо пользователю. Сообщение браузера: «Чтобы продолжить, установите этот флажок.». Форма не отправляется, а ошибка юзеру не показывается.
- 3. **[HIGH]** кнопка «Найти»: Кнопка перекрыта другим элементом (DIV.modal-up__background-close) — клик по ней визуально не срабатывает, попадает на перекрывающий блок.
- 4. **[HIGH]** кнопка «Принять»: Кнопка перекрыта другим элементом (DIV.modal-up__background-close) — клик по ней визуально не срабатывает, попадает на перекрывающий блок.
- 5. **[HIGH]** кнопка «Отклонить»: Кнопка перекрыта другим элементом (DIV.modal-up__background-close) — клик по ней визуально не срабатывает, попадает на перекрывающий блок.
- 6. **[HIGH]** кнопка «Настроить»: Кнопка перекрыта другим элементом (DIV.modal-up__background-close) — клик по ней визуально не срабатывает, попадает на перекрывающий блок.
- 7. **[MEDIUM]** Console / DevTools: Ошибка в консоли браузера: [warning] cdn.tailwindcss.com should not be used in production. To use Tailwind CSS in production, install it as a PostCSS plugin or use the Tailwind CLI: https
- 8. **[MEDIUM]** Console / DevTools: Ошибка в консоли браузера: [warning] Swiper Loop Warning: The number of slides is not enough for loop mode, it will be disabled and not function properly. You need to add more slides (or 
- 9. **[MEDIUM]** Console / DevTools: Ошибка в консоли браузера: [warning] [Meta Pixel] - You are sending a non-standard event 'Mainpage_krokit'. The preferred way to send these events is using trackCustom. See 'https://devel
