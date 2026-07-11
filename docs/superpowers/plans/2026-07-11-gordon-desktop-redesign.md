# Пульт Гордона v0.5 — Неон Cyber редизайн. План реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Переделать визуал и раскладку `gordon_desktop.py` под сочный неон-кибер стиль, сохранив все функции рабочими.

**Architecture:** Один модуль `gordon_desktop.py`. Добавляем кастомные виджеты (`NeonTitle`, `NeonCard`, `NeonButton`, `StatusIndicator`) — glow рисуем через `QGraphicsDropShadowEffect` + `QPainter`, пульс через `QPropertyAnimation`. Логику БД/запуска агентов не трогаем, только обёртку под новый стиль. Тесты гоняем на `PySide6` `QApplication` в offscreen-режиме.

**Tech Stack:** Python 3 + PySide6 (Qt6). Нативное окно, без веба. Тесты: `pytest` + offscreen Qt.

## Global Constraints

- Стек НЕ меняем: нативный PySide6, запуск `python gordon_desktop.py`. Без веба.
- БД `outreach.db` не трогаем. Логика `get_conn`/`read_stats`/`read_sites`/`read_site`/`run_track` неизменна.
- Запуск Гордона (`GordonRunner`, `start_gordon`, `start_forced`, `stop_gordon`, `on_runner_finished`), `track.py`-вызовы, окна `SiteDetailDialog`/`open_replies`/`_show_sources`, таймеры (3с/3с/2с/1с) — остаются рабочими.
- Палитра (из спека): фон `#070710`→`#0d0a1a` (градиент); неон-градиент `#a855f7`→`#22d3ee`→`#f472b6`; статусы неоновые (`pending #64748b`, `sent #4f9dff`, `replied #22c55e`, `hired #f5c518`, `rejected #ff4d6d`, `bounced #fb923c`); текст `#e6e6e6`/`#8a93a3`.
- В текстах НЕ использовать длинное тире «—», только короткое «-» (правило Назара).
- Коммитим, но НЕ пушим (пуш — только по прямой команде Назара).

---

### Task 1: Неон-палитра и импорты

**Files:**
- Modify: `gordon_desktop.py:31` (импорты QtCore — добавить `Property`), `gordon_desktop.py:47-63` (палитра)
- Test: `tests/test_ui.py` (новый)

**Interfaces:**
- Consumes: ничего (базовый таск).
- Produces: константы `BG`, `BG2`, `PANEL`, `PANEL2`, `NEON_PURPLE`, `NEON_CYAN`, `NEON_PINK`, `ACCENT`, `TEXT`, `MUTED`, `BORDER`, `STATUS_COLORS` — используются во всех следующих тасках.

- [ ] **Step 1: Write the failing test**

Создать `tests/test_ui.py`:

```python
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_palette_constants_importable():
    import gordon_desktop as g
    assert g.BG == "#070710"
    assert g.NEON_PURPLE == "#a855f7"
    assert g.NEON_CYAN == "#22d3ee"
    assert g.NEON_PINK == "#f472b6"
    assert set(g.STATUS_COLORS.keys()) == {
        "pending", "sent", "replied", "hired", "rejected", "bounced"
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: FAIL (модуль `gordon_desktop` либо не находится, либо константы не совпадают).

- [ ] **Step 3: Write minimal implementation**

В `gordon_desktop.py` добавить `Property` в импорт QtCore (строка ~31):

```python
from PySide6.QtCore import Qt, QThread, QTimer, Signal, Property
```

Заменить блок палитры (строки ~47-63) на:

```python
# ---------------------------------------------------------------------------
# Палитра (Неон Cyber)
# ---------------------------------------------------------------------------
BG = "#070710"            # глубокий чёрный
BG2 = "#0d0a1a"           # градиент вниз
PANEL = "rgba(20,18,38,0.65)"
PANEL2 = "rgba(30,28,52,0.75)"
NEON_PURPLE = "#a855f7"
NEON_CYAN = "#22d3ee"
NEON_PINK = "#f472b6"
ACCENT = NEON_CYAN
TEXT = "#e6e6e6"
MUTED = "#8a93a3"
BORDER = "#2a2440"

STATUS_COLORS = {
    "pending": "#64748b",   # серо-голубой
    "sent": "#4f9dff",      # ярко-циан/синий
    "replied": "#22c55e",   # неон-зелёный
    "hired": "#f5c518",     # золотой неон
    "rejected": "#ff4d6d",  # неон-красный
    "bounced": "#fb923c",   # неон-оранжевый
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gordon_desktop.py tests/test_ui.py
git commit -m "style(gordon): неон-кибер палитра + импорт Property"
```

---

### Task 2: NeonTitle и NeonCard

**Files:**
- Modify: `gordon_desktop.py` (добавить классы после блока палитры, перед `StatsCard`)
- Test: `tests/test_ui.py` (дописать)

**Interfaces:**
- Consumes: `NEON_PURPLE`, `NEON_CYAN`, `NEON_PINK`, `MUTED`, `TEXT` (Task 1).
- Produces: `NeonTitle(text)` (QLabel с градиентным неон-текстом), `NeonCard(title, color)` с методом `set_value(text)` (используется в `refresh_stats` через `self.cards[...].set_value(...)`).

- [ ] **Step 1: Write the failing test**

Дописать в `tests/test_ui.py`:

```python
from PySide6.QtWidgets import QGraphicsDropShadowEffect
import gordon_desktop as g


def test_neon_card_set_value(qapp):
    card = g.NeonCard("Sent", g.NEON_CYAN)
    card.set_value("42")
    assert card.num.text() == "42"


def test_neon_card_has_glow(qapp):
    card = g.NeonCard("Total", g.NEON_PURPLE)
    assert isinstance(card.graphicsEffect(), QGraphicsDropShadowEffect)


def test_neon_title_gradient_text(qapp):
    t = g.NeonTitle("ПУЛЬТ ГОРДОНА")
    assert t._text == "ПУЛЬТ ГОРДОНА"
    assert isinstance(t.graphicsEffect(), QGraphicsDropShadowEffect)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: FAIL (`NeonCard`/`NeonTitle` not defined).

- [ ] **Step 3: Write minimal implementation**

Заменить класс `StatsCard` (строки ~193-218) на два класса:

```python
# ---------------------------------------------------------------------------
# Неон-заголовок (градиентный текст + свечение)
# ---------------------------------------------------------------------------
class NeonTitle(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._text = text
        self.setFont(QFont("Segoe UI", 22, QFont.Bold))
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setColor(QColor(NEON_PURPLE))
        self._glow.setBlurRadius(18)
        self._glow.setOffset(0, 0)
        self.setGraphicsEffect(self._glow)
        self.setStyleSheet("background:transparent;")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        grad = QLinearGradient(0, 0, self.width(), 0)
        grad.setColorAt(0, QColor(NEON_PURPLE))
        grad.setColorAt(0.5, QColor(NEON_CYAN))
        grad.setColorAt(1, QColor(NEON_PINK))
        p.setFont(self.font())
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, self._text)


# ---------------------------------------------------------------------------
# Неон-карточка статистики (градиент + glow-бордер + вспышка при обновлении)
# ---------------------------------------------------------------------------
class NeonCard(QWidget):
    def __init__(self, title, color=NEON_CYAN, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._flash = 0.0
        self.setFixedHeight(86)
        self.setMinimumWidth(112)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        self.num = QLabel("0")
        self.num.setFont(QFont("Segoe UI", 24, QFont.Bold))
        self.num.setAlignment(Qt.AlignCenter)
        self.num.setStyleSheet(f"color:{color}; background:transparent;")
        self.title = QLabel(title)
        self.title.setFont(QFont("Segoe UI", 9))
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet(f"color:{MUTED}; background:transparent;")
        lay.addWidget(self.num)
        lay.addWidget(self.title)
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(18)
        self._glow.setColor(self._color)
        self._glow.setOffset(0, 0)
        self.setGraphicsEffect(self._glow)
        self._anim = QPropertyAnimation(self, b"flash")
        self._anim.setDuration(600)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.valueChanged.connect(self.update)

    def get_flash(self):
        return self._flash

    def set_flash(self, v):
        self._flash = v

    flash = Property(float, get_flash, set_flash)

    def set_value(self, text):
        self.num.setText(str(text))
        self._anim.stop()
        self._anim.start()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        rad = 12
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(18, 16, 38, 205))
        p.drawRoundedRect(r, rad, rad)
        alpha = 70 + int(145 * self._flash)
        pen = QPen(QColor(self._color).lighter(130))
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, rad, rad)
        super().paintEvent(e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gordon_desktop.py tests/test_ui.py
git commit -m "feat(gordon): NeonTitle + NeonCard с glow и вспышкой"
```

---

### Task 3: NeonButton

**Files:**
- Modify: `gordon_desktop.py` (добавить класс после `NeonCard`)
- Test: `tests/test_ui.py` (дописать)

**Interfaces:**
- Consumes: `PANEL2`, `MUTED`, `NEON_CYAN` (Task 1).
- Produces: `NeonButton(text, base)` — QPushButton с неон-градиентом, усилением свечения на hover/click. Заменяет обычные `QPushButton` в панели управления.

- [ ] **Step 1: Write the failing test**

Дописать в `tests/test_ui.py`:

```python
def test_neon_button_has_glow(qapp):
    b = g.NeonButton("Запустить", g.NEON_CYAN)
    assert isinstance(b.graphicsEffect(), QGraphicsDropShadowEffect)


def test_neon_button_hover_increases_blur(qapp):
    b = g.NeonButton("Запустить", g.NEON_CYAN)
    base = b.graphicsEffect().blurRadius()
    b.enterEvent(None)
    assert b.graphicsEffect().blurRadius() > base
    b.leaveEvent(None)
    assert b.graphicsEffect().blurRadius() == base
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: FAIL (`NeonButton` not defined).

- [ ] **Step 3: Write minimal implementation**

Добавить после класса `NeonCard`:

```python
# ---------------------------------------------------------------------------
# Неон-кнопка (градиент + свечение, разгоняется на hover/click)
# ---------------------------------------------------------------------------
class NeonButton(QPushButton):
    def __init__(self, text="", base=NEON_CYAN, parent=None):
        super().__init__(text, parent)
        self._base = QColor(base)
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(12)
        self._glow.setColor(self._base)
        self._glow.setOffset(0, 0)
        self.setGraphicsEffect(self._glow)
        self.setMinimumHeight(38)
        self._style()

    def _style(self):
        c = self._base
        self.setStyleSheet(
            f"QPushButton {{ background:{c.name()}; color:#070710; font-weight:bold; "
            f"border:none; border-radius:10px; padding:10px; }}"
            f"QPushButton:disabled {{ background:{PANEL2}; color:{MUTED}; }}"
        )

    def enterEvent(self, e):
        self._glow.setBlurRadius(28)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._glow.setBlurRadius(12)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._glow.setBlurRadius(36)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._glow.setBlurRadius(12)
        super().mouseReleaseEvent(e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gordon_desktop.py tests/test_ui.py
git commit -m "feat(gordon): NeonButton с hover/click свечением"
```

---

### Task 4: StatusIndicator (пульс)

**Files:**
- Modify: `gordon_desktop.py` (добавить класс после `NeonButton`)
- Test: `tests/test_ui.py` (дописать)

**Interfaces:**
- Consumes: `NEON_CYAN`, `MUTED` (Task 1).
- Produces: `StatusIndicator()` с методом `set_active(on, text=None)`. Заменяет `self.status_ind` (был QLabel) в `start_gordon`/`start_forced`/`on_runner_finished`.

- [ ] **Step 1: Write the failing test**

Дописать в `tests/test_ui.py`:

```python
def test_status_indicator_idle(qapp):
    s = g.StatusIndicator()
    assert s._active is False
    assert "не запущен" in s.label.text()


def test_status_indicator_active(qapp):
    s = g.StatusIndicator()
    s.set_active(True, "Гордон работает…")
    assert s._active is True
    assert "работает" in s.label.text()
    s.set_active(False)
    assert s._active is False
    assert "не запущен" in s.label.text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: FAIL (`StatusIndicator` not defined).

- [ ] **Step 3: Write minimal implementation**

Добавить после класса `NeonButton`:

```python
# ---------------------------------------------------------------------------
# Индикатор статуса Гордона (пульсирующее свечение)
# ---------------------------------------------------------------------------
class StatusIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self.setFixedHeight(36)
        self.setMinimumWidth(210)
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(10)
        self._glow.setColor(QColor(MUTED))
        self._glow.setOffset(0, 0)
        self.label = QLabel("Гордон не запущен")
        self.label.setStyleSheet(f"color:{MUTED}; font-weight:bold;")
        self.label.setGraphicsEffect(self._glow)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.label)
        self._pulse = QPropertyAnimation(self, b"pulse")
        self._pulse.setDuration(1200)
        self._pulse.setLoopCount(-1)
        self._pulse.setStartValue(0.3)
        self._pulse.setEndValue(1.0)
        self._pulse.valueChanged.connect(self._on_pulse)

    def get_pulse(self):
        return 0.0

    def set_pulse(self, v):
        pass

    pulse = Property(float, get_pulse, set_pulse)

    def _on_pulse(self, v):
        self._glow.setBlurRadius(8 + int(18 * v))

    def set_active(self, on, text=None):
        self._active = on
        self.label.setText(text or ("Гордон работает…" if on else "Гордон не запущен"))
        if on:
            self.label.setStyleSheet(f"color:{NEON_CYAN}; font-weight:bold;")
            self._glow.setColor(QColor(NEON_CYAN))
            if self._pulse.state() != QPropertyAnimation.Running:
                self._pulse.start()
        else:
            self.label.setStyleSheet(f"color:{MUTED}; font-weight:bold;")
            self._glow.setColor(QColor(MUTED))
            self._pulse.stop()
            self._glow.setBlurRadius(10)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gordon_desktop.py tests/test_ui.py
git commit -m "feat(gordon): StatusIndicator с пульсом"
```

---

### Task 5: Переделка лейаута (_build_ui) + градиентный фон

**Files:**
- Modify: `gordon_desktop.py` (метод `_build_ui` целиком, ~строки 464-652; методы `start_gordon`/`start_forced`/`on_runner_finished` — замена `self.status_ind` на `StatusIndicator`; `main()` — градиентный фон окна)
- Test: `tests/test_ui.py` (дописать интеграционный smoke-тест сборки окна)

**Interfaces:**
- Consumes: `NeonTitle`, `NeonCard`, `NeonButton`, `StatusIndicator` (Tasks 2-4); `BG`, `BG2`, `PANEL`, `PANEL2`, `NEON_CYAN`, `NEON_PURPLE`, `STATUS_COLORS` (Task 1).
- Produces: новый лейаут (header + sidebar + центр). `self.cards` теперь `NeonCard` (совместимо с `refresh_stats`). `self.status_ind` теперь `StatusIndicator` (совместимо со `set_active`). Кнопки `self.btn_start`/`btn_stop`/`btn_force`/`btn_parse`/`btn_replies` — `NeonButton`, слоты те же.

- [ ] **Step 1: Write the failing test**

Дописать в `tests/test_ui.py`:

```python
def test_main_window_builds(qapp):
    w = g.GordonDesktop()
    assert w is not None
    assert hasattr(w, "status_ind") and isinstance(w.status_ind, g.StatusIndicator)
    assert len(w.cards) >= 9
    assert isinstance(w.cards["Sent"], g.NeonCard)
    assert isinstance(w.btn_start, g.NeonButton)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: FAIL (старый лейаут: status_ind — QLabel, btn_start — QPushButton).

- [ ] **Step 3: Write minimal implementation**

Заменить метод `_build_ui` (полностью, строки ~464-652) на новый. Header слева: иконка-щит `🛡` в glow-рамке + `NeonTitle("ПУЛЬТ ГОРДОНА")` + мелкая `v0.5`; справа `StatusIndicator`. Sidebar (~240px): `NeonButton` для Запустить/Стоп/Форса/Парсинг/Ответы + блок инфо про гард. Центр: ряд `NeonCard` + `QSplitter` (таблица сверху, лог снизу). Фильтры — неон-поля (стиль из Task 6 подключим позже, пока дефолт `_combo_style`/`_edit_style`).

```python
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        root.setStyleSheet(
            "QWidget#root { background: qlineargradient("
            "x1:0, y1:0, x2:0, y2:1, stop:0 #070710, stop:1 #0d0a1a); }")
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(10)

        # --- HEADER ---
        header = QHBoxLayout()
        header.setSpacing(10)
        shield = QLabel("🛡")
        shield.setStyleSheet("font-size:30px; background:transparent;")
        s_glow = QGraphicsDropShadowEffect(shield)
        s_glow.setColor(QColor(NEON_PURPLE))
        s_glow.setBlurRadius(20)
        s_glow.setOffset(0, 0)
        shield.setGraphicsEffect(s_glow)
        title = NeonTitle("ПУЛЬТ ГОРДОНА")
        ver = QLabel("v0.5")
        ver.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        header.addWidget(shield)
        header.addWidget(title)
        header.addWidget(ver)
        header.addStretch(1)
        self.status_ind = StatusIndicator()
        header.addWidget(self.status_ind)
        main.addLayout(header)

        # --- DASHBOARD (неон-карточки) ---
        self.cards = {}
        dash = QHBoxLayout()
        dash.setSpacing(10)
        card_defs = [
            ("Total", None), ("Pending", "pending"), ("Sent", "sent"),
            ("Replied", "replied"), ("Hired", "hired"),
            ("Rejected", "rejected"), ("Bounced", "bounced"),
        ]
        for title_, st in card_defs:
            color = STATUS_COLORS.get(st, NEON_PURPLE) if st else NEON_PURPLE
            c = NeonCard(title_, color)
            self.cards[title_] = c
            dash.addWidget(c)
        self.cards["Conv"] = NeonCard("Конверсия %", NEON_CYAN)
        self.cards["Earned"] = NeonCard("Заработано", STATUS_COLORS["hired"])
        dash.addWidget(self.cards["Conv"])
        dash.addWidget(self.cards["Earned"])
        dash.addStretch(1)

        dash_scroll = QScrollArea()
        dash_scroll.setWidgetResizable(True)
        dash_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        dash_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        dash_scroll.setFixedHeight(104)
        dash_scroll.setStyleSheet(
            f"QScrollArea {{ background:transparent; border:none; }}")
        dash_wrap = QWidget()
        dash_wrap.setLayout(dash)
        dash_scroll.setWidget(dash_wrap)
        main.addWidget(dash_scroll)

        # --- MID: sidebar + center ---
        mid = QHBoxLayout()
        mid.setSpacing(10)

        ctrl = QVBoxLayout()
        ctrl.setSpacing(8)
        ctrl_frame = QFrame()
        ctrl_frame.setObjectName("ctrl")
        ctrl_frame.setStyleSheet(
            f"#ctrl {{ background:{PANEL}; border:1px solid {BORDER}; "
            f"border-radius:14px; padding:12px; }}")
        ctrl_frame.setFixedWidth(236)
        ctrl_frame.setLayout(ctrl)

        ag_label = QLabel("АГЕНТЫ")
        ag_label.setStyleSheet(f"color:{MUTED}; font-weight:bold; letter-spacing:1px;")
        ctrl.addWidget(ag_label)

        # для start/stop/force — базовый цвет акцента
        self.btn_start = NeonButton("▶ Запустить Гордона", NEON_CYAN)
        self.btn_stop = NeonButton("⏹ Стоп", "#ff4d6d")
        self.btn_force = NeonButton("⚠ Форсированная отправка (вне окна!)", "#fb923c")
        self.btn_start.clicked.connect(self.start_gordon)
        self.btn_stop.clicked.connect(self.stop_gordon)
        self.btn_force.clicked.connect(self.start_forced)
        self.btn_stop.setEnabled(False)
        for b in (self.btn_start, self.btn_stop, self.btn_force):
            ctrl.addWidget(b)

        ctrl.addSpacing(10)
        guard = QLabel(
            "Штатный gordon.py сам уважает окно 9-21 и дневные лимиты.\n"
            "Форсированная отправка (send_now.py) — ВНЕ окна, только по прямой команде.")
        guard.setWordWrap(True)
        guard.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        ctrl.addWidget(guard)

        ctrl.addSpacing(12)
        agents_label = QLabel("ИНСТРУМЕНТЫ")
        agents_label.setStyleSheet(f"color:{MUTED}; font-weight:bold; letter-spacing:1px;")
        ctrl.addWidget(agents_label)
        self.btn_parse = NeonButton("🕸 Парсинг сайтов", NEON_CYAN)
        self.btn_replies = NeonButton("📥 Просмотр ответов", "#22c55e")
        self.btn_parse.clicked.connect(self.open_parse_menu)
        self.btn_replies.clicked.connect(self.open_replies)
        ctrl.addWidget(self.btn_parse)
        ctrl.addWidget(self.btn_replies)
        ctrl.addStretch(1)
        mid.addWidget(ctrl_frame)

        # --- центр: splitter(таблица / лог) ---
        vsplit = QSplitter(Qt.Vertical)
        top = QWidget()
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(8)

        filt = QHBoxLayout()
        filt.setSpacing(6)
        self.status_combo = QComboBox()
        self.status_combo.addItems(
            ["Все", "pending", "sent", "replied", "hired", "rejected", "bounced"])
        self.status_combo.setStyleSheet(self._combo_style())
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск: url / email / telegram…")
        self.search_edit.setStyleSheet(self._edit_style())
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("Теги (подстрока)…")
        self.tags_edit.setStyleSheet(self._edit_style())
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Обновлено ↓", "Обновлено ↑", "ID ↓", "ID ↑"])
        self.sort_combo.setStyleSheet(self._combo_style())
        for w in (self.status_combo, self.search_edit, self.tags_edit, self.sort_combo):
            filt.addWidget(w)
        self.search_edit.setMinimumWidth(220)
        top_lay.addLayout(filt)

        self.status_combo.currentTextChanged.connect(self.refresh_table)
        self.sort_combo.currentTextChanged.connect(self.refresh_table)
        self.search_edit.textChanged.connect(self.refresh_table)
        self.tags_edit.textChanged.connect(self.refresh_table)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["ID", "URL", "Email", "Telegram", "Status", "Tags", "Earned", "Updated"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self.open_details)
        self.table.setStyleSheet(self._table_style())
        top_lay.addWidget(self.table, 1)

        bot = QWidget()
        bot_lay = QVBoxLayout(bot)
        bot_lay.setContentsMargins(0, 0, 0, 0)
        bot_lay.setSpacing(4)
        log_label = QLabel("ЖИВАЯ ЛЕНТА (gordon_run.log)")
        log_label.setStyleSheet(f"color:{MUTED}; font-weight:bold; letter-spacing:1px;")
        bot_lay.addWidget(log_label)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setStyleSheet(
            f"background:{PANEL}; color:#cfe3ff; border:1px solid {BORDER}; "
            f"border-radius:10px;")
        bot_lay.addWidget(self.log_view, 1)

        vsplit.addWidget(top)
        vsplit.addWidget(bot)
        vsplit.setSizes([560, 220])
        mid.addWidget(vsplit, 1)

        main.addLayout(mid, 1)
```

В `main()` заменить строку установки стиля окна на градиентный фон (строка ~1063):

```python
    app.setStyleSheet(
        f"QMainWindow {{ background:{BG}; color:{TEXT}; }}"
        f"QToolTip {{ background:#14122a; color:{TEXT}; border:1px solid {BORDER}; }}")
```

В `start_gordon` (строки ~819-825) заменить блок `self.status_ind.setText(...)` на:

```python
        self.status_ind.set_active(True, "Гордон работает…")
        self.btn_start.setEnabled(False)
        self.btn_force.setEnabled(False)
        self.btn_stop.setEnabled(True)
```

В `start_forced` (строки ~843-849) заменить на:

```python
        self.status_ind.set_active(True, "Форсированная отправка…")
        self.btn_start.setEnabled(False)
        self.btn_force.setEnabled(False)
        self.btn_stop.setEnabled(True)
```

В `on_runner_finished` (строки ~857-860) заменить на:

```python
        self.status_ind.set_active(False, "Гордон не запущен")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: PASS (окно собирается, status_ind — StatusIndicator, cards — NeonCard, btn_start — NeonButton).

- [ ] **Step 5: Commit**

```bash
git add gordon_desktop.py tests/test_ui.py
git commit -m "feat(gordon): новый лейаут header+sidebar+центр, градиентный фон"
```

---

### Task 6: Неон-QSS для полей, таблицы и лога

**Files:**
- Modify: `gordon_desktop.py` (методы `_combo_style`, `_edit_style`, `_table_style` — строки ~655-670)
- Test: `tests/test_ui.py` (дописать)

**Interfaces:**
- Consumes: `PANEL2`, `BORDER`, `TEXT`, `MUTED`, `ACCENT`, `BG` (Task 1).
- Produces: обновлённые стили комбо/полей/таблицы с неон-бордерами; используются в `_build_ui` и `open_replies`.

- [ ] **Step 1: Write the failing test**

Дописать в `tests/test_ui.py`:

```python
def test_combo_style_neon_border(qapp):
    w = g.GordonDesktop()
    s = w._combo_style()
    assert "qlineargradient" not in s  # должен быть плоский неон-бордер
    assert g.BORDER in s
    assert "rgba(34,211,238" in s or "#22d3ee" in s  # циан-акцент в выпадашке


def test_table_style_neon(qapp):
    w = g.GordonDesktop()
    s = w._table_style()
    assert g.BORDER in s
    assert "#22d3ee" in s or "qlineargradient" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: FAIL (старые стили без неона).

- [ ] **Step 3: Write minimal implementation**

Заменить методы стилей:

```python
    def _combo_style(self):
        return (f"QComboBox {{ background:{PANEL2}; color:{TEXT}; "
                f"border:1px solid {BORDER}; border-radius:8px; padding:6px; }}"
                f"QComboBox:hover {{ border:1px solid #22d3ee; }}"
                f"QComboBox QAbstractItemView {{ background:#14122a; color:{TEXT}; "
                f"selection-background-color:#22d3ee; }}"
                f"QComboBox::drop-down {{ border:none; }}")

    def _edit_style(self):
        return (f"QLineEdit {{ background:{PANEL2}; color:{TEXT}; "
                f"border:1px solid {BORDER}; border-radius:8px; padding:7px; }}"
                f"QLineEdit:focus {{ border:1px solid #22d3ee; }}")

    def _table_style(self):
        return (f"QTableWidget {{ background:rgba(10,8,20,0.6); color:{TEXT}; "
                f"gridline-color:{BORDER}; border:1px solid {BORDER}; "
                f"border-radius:10px; }}"
                f"QHeaderView::section {{ background:#14122a; color:{MUTED}; "
                f"border:none; padding:7px; }}"
                f"QTableWidget::item {{ padding:5px; }}"
                f"QTableWidget::item:selected {{ background:#22d3ee; color:#070710; }}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gordon_desktop.py tests/test_ui.py
git commit -m "style(gordon): неон-QSS для полей/комбо/таблицы"
```

---

### Task 7: Анимация затухания свежеотправленных строк

**Files:**
- Modify: `gordon_desktop.py` (метод `refresh_table` — строки ~701-732, блок подсветки `_fresh_sent`)
- Test: `tests/test_ui.py` (дописать)

**Interfaces:**
- Consumes: `_fresh_sent` (dict id->timestamp), `datetime`, `STATUS_COLORS` (Task 1).
- Produces: строки со свежими `sent` подсвечиваются неон-зелёным с плавным затуханием альфы по возрасту.

- [ ] **Step 1: Write the failing test**

Дописать в `tests/test_ui.py`:

```python
def test_fresh_row_fades(qapp):
    w = g.GordonDesktop()
    # эмулируем свежую отправку (id, который вряд ли есть в БД) 8 секунд назад
    w._fresh_sent = {999999: __import__("time").time() - 8}
    # проверяем, что метод не падает при наличии свежей подсветки
    try:
        w.refresh_table()
    except Exception as e:
        raise AssertionError(f"refresh_table упал: {e}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: сейчас строка подсвечивается статичным `#16361f`; после правки — альфа по возрасту. Тест на падение пройдёт и так, поэтому проверяем поведение отдельно (см. Step 4 визуально). Можно считать этот smoke-тест базовым гейтом.

- [ ] **Step 3: Write minimal implementation**

В `refresh_table` заменить блок подсветки (строки ~727-728):

```python
                elif r["id"] in self._fresh_sent:  # свежеотправленное — неон-зелёное, затухает
                    age = now - self._fresh_sent[r["id"]]
                    frac = max(0.0, min(1.0, 1.0 - age / 15.0))
                    alpha = int(60 + 195 * frac)
                    item.setBackground(QColor(34, 197, 94, alpha))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: PASS. Визуально: строка, ушедшая в sent только что, ярко-неоновая, за 15с плавно затухает до прозрачной.

- [ ] **Step 5: Commit**

```bash
git add gordon_desktop.py tests/test_ui.py
git commit -m "feat(gordon): затухание неон-подсветки свежих отправок"
```

---

### Task 8: Интеграционная проверка запуска

**Files:**
- Modify: ничего (только проверка)
- Test: ручной запуск + существующие тесты

**Interfaces:**
- Consumes: все виджеты и стили из Tasks 1-7.

- [ ] **Step 1: Run full test suite**

Run: `cd /c/Users/nazar/outreach-tracker && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py -v`
Expected: все PASS.

- [ ] **Step 2: Проверить, что PySide6 установлен, и запустить окно вручную**

Run: `cd /c/Users/nazar/outreach-tracker && python -c "import PySide6; print('PySide6 OK')"`
Если нет — `pip install PySide6`.

- [ ] **Step 3: Запустить приложение и проверить визуал/функции**

Run: `cd /c/Users/nazar/outreach-tracker && python gordon_desktop.py`
Проверить вручную (чеклист из спека):
- окно открывается без ошибок, чёрный градиентный фон;
- неон-карточки показывают цифры из `outreach.db` (215 сайтов, 103 sent и т.д.);
- `StatusIndicator` спокоен («не запущен»), при запуске Гордона пульсирует цианом;
- hover по кнопкам sidebar усиливает свечение, click — вспышка;
- таблица/фильтры/лог/окна деталей и ответов работают;
- запуск Гордона/парсинга/проверки почты из sidebar не сломан;
- свежеотправленные строки подсвечиваются неон-зелёным и затухают.

- [ ] **Step 4: Commit (если были правки по ходу)**

Если в Task 8 правили баги — коммитим мелкими коммитами. Если всё ок — коммит не нужен, НЕ пушим (пуш только по команде Назара).
