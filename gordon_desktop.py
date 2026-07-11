#!/usr/bin/env python3
"""
gordon_desktop.py - НАТИВНЫЙ «Пульт Гордона» (PySide6 / Qt6).

Оконное приложение (НЕ веб) для управления армией агентов Гордона:
- дашборд статистики (живой, каждые 3 сек)
- таблица сайтов с фильтрами и деталями (кнопки дёргают track.py)
- запуск/стоп Гордона в отдельном потоке (без блокировки UI)
- живая лента лога (tail gordon_run.log) + перехват отправок из БД

Стек: Python 3 + PySide6. Чтение БД напрямую через sqlite3 (только SELECT +
вызовы track.py). Запуск Гордона через subprocess (gordon.py / send_now.py).

Сборка в .exe позже: pyinstaller --noconsole gordon_desktop.py
"""

import os
import re
import sys
import sqlite3
import subprocess
from datetime import datetime
from urllib.parse import urlparse

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QComboBox, QLineEdit, QFrame,
    QDialog, QTextEdit, QPlainTextEdit, QMessageBox, QInputDialog, QScrollArea,
    QSplitter, QHeaderView, QStyle, QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt, QThread, QTimer, Signal, Property, QPropertyAnimation
from PySide6.QtGui import (
    QFont, QColor, QIcon, QGuiApplication, QPainter, QLinearGradient, QPen,
)

# ---------------------------------------------------------------------------
# Пути (всё рядом с приложением)
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "outreach.db")
LOG_PATH = os.path.join(HERE, "gordon_run.log")
ENV_PATH = os.path.join(HERE, ".env")
LETTER_PATH = os.path.join(HERE, "letter.txt")
TRACK = os.path.join(HERE, "track.py")
GORDON = os.path.join(HERE, "gordon.py")
SEND_NOW = os.path.join(HERE, "send_now.py")

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

# Глобально: utf-8, чтобы кириллица не плыла в консоли Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------------------
# Работа с БД (только SELECT; DELETE journal; try/except на busy)
# ---------------------------------------------------------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=2.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
    except Exception:
        pass
    return conn


def read_stats():
    """Одним запросом - сводка по статусам + заработок."""
    try:
        conn = get_conn()
        by_status = {}
        for r in conn.execute(
            "SELECT status, COUNT(*) n FROM sites GROUP BY status"
        ).fetchall():
            by_status[r["status"]] = r["n"]
        earned = conn.execute(
            "SELECT COALESCE(SUM(amount_earned), 0) FROM sites WHERE status='hired'"
        ).fetchone()[0] or 0
        conn.close()
        total = sum(by_status.values())
        sent_pool = (
            by_status.get("sent", 0) + by_status.get("replied", 0)
            + by_status.get("hired", 0) + by_status.get("rejected", 0)
            + by_status.get("bounced", 0)
        )
        hired = by_status.get("hired", 0)
        conv = (hired / sent_pool * 100) if sent_pool else 0.0
        return {
            "total": total,
            "pending": by_status.get("pending", 0),
            "sent": by_status.get("sent", 0),
            "replied": by_status.get("replied", 0),
            "hired": by_status.get("hired", 0),
            "rejected": by_status.get("rejected", 0),
            "bounced": by_status.get("bounced", 0),
            "earned": earned,
            "conversion": conv,
        }
    except sqlite3.OperationalError:
        # БД занята Гордоном - пропускаем итерацию
        return None
    except Exception:
        return None


def read_sites(status_filter, search, tags, sort_mode):
    """Возвращает список dict для таблицы. Фильтры комбинируются через AND."""
    try:
        conn = get_conn()
        q = ("SELECT id, url, email, telegram, status, tags, "
             "amount_earned, updated_at FROM sites WHERE 1=1")
        params = []
        if status_filter and status_filter != "Все":
            q += " AND status=?"
            params.append(status_filter)
        if search:
            s = f"%{search.lower()}%"
            q += (" AND (LOWER(url) LIKE ? OR LOWER(email) LIKE ? "
                  "OR LOWER(telegram) LIKE ?)")
            params += [s, s, s]
        if tags:
            q += " AND LOWER(tags) LIKE ?"
            params.append(f"%{tags.lower()}%")

        if sort_mode == "ID ↑":
            q += " ORDER BY id ASC"
        elif sort_mode == "ID ↓":
            q += " ORDER BY id DESC"
        elif sort_mode == "Обновлено ↑":
            q += " ORDER BY updated_at ASC, id ASC"
        else:  # "Обновлено ↓" (по умолчанию)
            q += " ORDER BY updated_at DESC, id DESC"

        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return None
    except Exception:
        return None


def read_site(id_):
    try:
        conn = get_conn()
        r = conn.execute("SELECT * FROM sites WHERE id=?", (id_,)).fetchone()
        conn.close()
        return dict(r) if r else None
    except Exception:
        return None


def run_track(args):
    """Вызывает track.py (НЕ трогает БД напрямую). Возвращает (ok, output)."""
    try:
        res = subprocess.run(
            [sys.executable, TRACK] + args,
            cwd=HERE, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        return (res.returncode == 0, (res.stdout or "") + (res.stderr or ""))
    except Exception as e:
        return (False, str(e))


def domain_of(url):
    d = urlparse(url or "").netloc
    return d or (url or "")


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
        self._anim.valueChanged.connect(self._on_flash)

    def get_flash(self):
        return self._flash

    def set_flash(self, v):
        self._flash = v

    flash = Property(float, get_flash, set_flash)

    def set_value(self, text):
        self.num.setText(str(text))
        self._anim.stop()
        self._anim.start()

    def _on_flash(self):
        self._glow.setBlurRadius(18 + int(14 * self._flash))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        rad = 12
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(18, 16, 38, 205))
        p.drawRoundedRect(r, rad, rad)
        # вспышка при set_value(): бордер ярче и толще, glow разгоняется
        pen = QPen(QColor(self._color).lighter(130 + int(50 * self._flash)))
        pen.setWidth(2 + int(2 * self._flash))
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, rad, rad)
        super().paintEvent(e)


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


# ---------------------------------------------------------------------------
# Поток запуска Гордона (gordon.py / send_now.py)
# ---------------------------------------------------------------------------
class GordonRunner(QThread):
    log_line = Signal(str)        # служебные строки приложения
    finished = Signal(int)        # код завершения

    def __init__(self, script, label):
        super().__init__()
        self.script = script
        self.label = label
        self._proc = None
        self._stop = False

    def run(self):
        try:
            self.log_line.emit(f"[APP] Запуск: {self.label} ({os.path.basename(self.script)})")
            self._proc = subprocess.Popen(
                [sys.executable, self.script],
                cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            # читаем stdout построчно; сам лог Гордона идёт в ленту через tail файла,
            # здесь только служебно отмечаем старт/завершение (без дублей в ленте)
            for _ in self._proc.stdout:
                if self._stop:
                    break
            self._proc.wait()
            code = self._proc.returncode
            self.log_line.emit(f"[APP] {self.label} завершён, код {code}")
            self.finished.emit(code if code is not None else 0)
        except Exception as e:
            self.log_line.emit(f"[APP] Ошибка запуска {self.label}: {e}")
            self.finished.emit(-1)

    def stop(self):
        self._stop = True
        if self._proc is not None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except Exception:
                    self._proc.kill()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Окно деталей сайта
# ---------------------------------------------------------------------------
class SiteDetailDialog(QDialog):
    def __init__(self, site_id, parent=None):
        super().__init__(parent)
        self.site_id = site_id
        self.setWindowTitle(f"Сайт #{site_id}")
        self.setMinimumSize(560, 520)
        self.setStyleSheet(f"QDialog {{ background:{BG}; color:{TEXT}; }}")
        self._build()

    def _build(self):
        data = read_site(self.site_id)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)

        if not data:
            lay.addWidget(QLabel("Сайт не найден."))
            return

        def field(label, value):
            row = QHBoxLayout()
            lab = QLabel(label)
            lab.setFixedWidth(90)
            lab.setStyleSheet(f"color:{MUTED};")
            val = QLabel(str(value if value is not None else "-"))
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(lab)
            row.addWidget(val, 1)
            return row

        # статус-бейдж
        st = data.get("status", "")
        color = STATUS_COLORS.get(st, MUTED)
        badge = QLabel(f"  {st.upper()}  ")
        badge.setStyleSheet(
            f"background:{color}; color:#0f1115; font-weight:bold; "
            f"border-radius:6px; padding:2px 8px;"
        )
        badge.setFixedWidth(110)

        head = QHBoxLayout()
        head.addWidget(QLabel(f"#{data['id']}"))
        head.addWidget(badge)
        head.addStretch(1)
        lay.addLayout(head)

        lay.addLayout(field("URL", data.get("url", "")))
        lay.addLayout(field("Email", data.get("email", "")))
        lay.addLayout(field("Telegram", data.get("telegram", "")))
        lay.addLayout(field("Tags", data.get("tags", "")))
        lay.addLayout(field("Source", data.get("source", "")))
        lay.addLayout(field("Earned",
                            f"BYN {data.get('amount_earned') or 0:.2f}"
                            if data.get("amount_earned") else "-"))
        lay.addLayout(field("Создан", data.get("created_at", "")))
        lay.addLayout(field("Обновлён", data.get("updated_at", "")))

        # notes - полностью, с переносами
        lay.addWidget(QLabel("Заметки / аудит:"))
        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(data.get("notes") or "(пусто)")
        notes.setStyleSheet(
            f"background:{PANEL}; color:{TEXT}; border:1px solid #262b36; "
            f"border-radius:8px;"
        )
        notes.setFont(QFont("Consolas", 10))
        lay.addWidget(notes, 1)

        # кнопки действий
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        def mk(text, slot, color=ACCENT):
            b = QPushButton(text)
            b.setStyleSheet(
                f"QPushButton {{ background:{PANEL2}; color:{TEXT}; "
                f"border:1px solid {color}; border-radius:8px; padding:6px 10px; }}"
                f"QPushButton:hover {{ background:{color}; color:#0f1115; }}"
            )
            b.clicked.connect(slot)
            return b

        btn_row.addWidget(mk("reply", self._reply, STATUS_COLORS["replied"]))
        btn_row.addWidget(mk("hired", self._hired, STATUS_COLORS["hired"]))
        btn_row.addWidget(mk("rejected", self._rejected, STATUS_COLORS["rejected"]))
        btn_row.addWidget(mk("bounce", self._bounce, STATUS_COLORS["bounced"]))
        btn_row.addWidget(mk("note", self._note, MUTED))
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        close_b = QPushButton("Закрыть")
        close_b.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:#0f1115; font-weight:bold; "
            f"border-radius:8px; padding:8px; }}"
        )
        close_b.clicked.connect(self.accept)
        lay.addWidget(close_b)

    def _refresh_parent(self):
        if self.parent():
            self.parent().refresh_all()

    def _reply(self):
        ok, out = run_track(["reply", str(self.site_id)])
        self._after(ok, out)

    def _rejected(self):
        ok, out = run_track(["rejected", str(self.site_id)])
        self._after(ok, out)

    def _bounce(self):
        ok, out = run_track(["bounce", str(self.site_id)])
        self._after(ok, out)

    def _hired(self):
        amt, good = QInputDialog.getDouble(
            self, "Заработок", "Сумма (BYN):", 0.0, 0, 100000, 2)
        if not good:
            return
        ok, out = run_track(["hired", str(self.site_id), "--amount", str(amt)])
        self._after(ok, out)

    def _note(self):
        text, good = QInputDialog.getText(self, "Заметка", "Текст заметки:")
        if not good or not text.strip():
            return
        ok, out = run_track(["note", str(self.site_id), text.strip()])
        self._after(ok, out)

    def _after(self, ok, out):
        if not ok:
            QMessageBox.warning(self, "Ошибка", f"track.py:\n{out[:500]}")
        else:
            self._refresh_parent()
            # перечитать данные окна
            self._rebuild()

    def _rebuild(self):
        # перестроим содержимое без закрытия диалога
        old = self.layout()
        if old:
            while old.count():
                w = old.takeAt(0).widget()
                if w:
                    w.deleteLater()
        self._build()


# ---------------------------------------------------------------------------
# Главное окно
# ---------------------------------------------------------------------------
class GordonDesktop(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Пульт Гордона")
        self.showFullScreen()
        try:
            icon = QApplication.style().standardIcon(
                QStyle.StandardPixmap.SP_ComputerIcon)
            self.setWindowIcon(icon)
        except Exception:
            pass

        self.runner = None
        self._log_pos = 0
        self._seen_sent = set()        # id уже отправленных (для ленты)
        self._fresh_sent = {}          # id -> timestamp (для подсветки в таблице)

        self._build_ui()
        self._init_log_tail()
        self.refresh_all()

        # таймеры
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self.refresh_stats)
        self.stats_timer.start(3000)

        self.table_timer = QTimer(self)
        self.table_timer.timeout.connect(self.refresh_table)
        self.table_timer.start(3000)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_sent)
        self.poll_timer.start(2000)

        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self.tail_log)
        self.log_timer.start(1000)

    # ----- построение UI -----
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

        # для start/stop/force - базовый цвет акцента
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
            "Форсированная отправка (send_now.py) - ВНЕ окна, только по прямой команде.")
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

    # ----- стили -----
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
                f"QHeaderView::corner {{ background:#14122a; border:none; }}"
                f"QTableWidget::item {{ padding:5px; }}"
                f"QTableWidget::item:selected {{ background:#22d3ee; color:#070710; }}")

    # ----- обновления -----
    def refresh_all(self):
        self.refresh_stats()
        self.refresh_table()
        self.poll_sent(initial=True)

    def refresh_stats(self):
        s = read_stats()
        if s is None:
            return
        self.cards["Total"].set_value(s["total"])
        self.cards["Pending"].set_value(s["pending"])
        self.cards["Sent"].set_value(s["sent"])
        self.cards["Replied"].set_value(s["replied"])
        self.cards["Hired"].set_value(s["hired"])
        self.cards["Rejected"].set_value(s["rejected"])
        self.cards["Bounced"].set_value(s["bounced"])
        self.cards["Conv"].set_value(f"{s['conversion']:.1f}")
        self.cards["Earned"].set_value(f"BYN {s['earned']:.2f}")

    def refresh_table(self):
        status = self.status_combo.currentText()
        search = self.search_edit.text().strip()
        tags = self.tags_edit.text().strip()
        sort_mode = self.sort_combo.currentText()
        rows = read_sites(status, search, tags, sort_mode)
        if rows is None:
            return

        now = datetime.now().timestamp()
        # чистим протухшие подсветки
        self._fresh_sent = {k: v for k, v in self._fresh_sent.items() if now - v < 15}

        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            st = r.get("status", "")
            color = QColor(STATUS_COLORS.get(st, MUTED))
            vals = [
                str(r.get("id", "")),
                r.get("url") or "",
                r.get("email") or "",
                r.get("telegram") or "",
                st,
                r.get("tags") or "",
                (f"BYN {r.get('amount_earned') or 0:.2f}"
                 if r.get("amount_earned") else "-"),
                (r.get("updated_at") or "")[:19],
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setForeground(QColor(TEXT))
                if c == 4:  # статус - бейдж цветом
                    item.setBackground(color)
                    item.setForeground(QColor("#0f1115"))
                    item.setFont(QFont("Segoe UI", 9, QFont.Bold))
                elif r["id"] in self._fresh_sent:  # свежеотправленное - неон-зелёное, затухает
                    age = now - self._fresh_sent[r["id"]]
                    frac = max(0.0, min(1.0, 1.0 - age / 15.0))
                    alpha = int(60 + 195 * frac)
                    item.setBackground(QColor(34, 197, 94, alpha))
                self.table.setItem(i, c, item)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(1, max(220, self.table.columnWidth(1)))
        self.table.setColumnWidth(2, max(180, self.table.columnWidth(2)))

    def poll_sent(self, initial=False):
        """Перехват сайтов, ушедших в sent (pending→sent). Дополняет ленту."""
        try:
            conn = get_conn()
            rows = conn.execute(
                "SELECT id, email, url FROM sites WHERE status='sent'"
            ).fetchall()
            conn.close()
        except sqlite3.OperationalError:
            return
        except Exception:
            return

        for r in rows:
            if r["id"] in self._seen_sent:
                continue
            self._seen_sent.add(r["id"])
            if not initial:
                d = domain_of(r["url"])
                addr = r["email"] or "-"
                self.append_log(
                    f"📨 Отправлено #{r['id']} → {addr} ({d})")
                self._fresh_sent[r["id"]] = datetime.now().timestamp()
                # подсветим в таблице при следующем refresh
        if not initial:
            self.refresh_stats()

    # ----- лог -----
    def _init_log_tail(self):
        try:
            if os.path.exists(LOG_PATH):
                with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
                for ln in lines[-200:]:
                    if ln.strip():
                        self.log_view.appendPlainText(ln)
                self._log_pos = os.path.getsize(LOG_PATH)
            else:
                self._log_pos = 0
        except Exception:
            self._log_pos = 0
        self._scroll_log()

    def tail_log(self):
        try:
            if not os.path.exists(LOG_PATH):
                return
            size = os.path.getsize(LOG_PATH)
            if size < self._log_pos:
                # файл ужался/пересоздан - начинаем сначала
                self._log_pos = 0
            with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._log_pos)
                data = f.read()
                self._log_pos = f.tell()
            for ln in data.splitlines():
                if ln.strip():
                    self.log_view.appendPlainText(ln)
            # ограничим размер ленты
            if self.log_view.blockCount() > 3000:
                cursor = self.log_view.textCursor()
                cursor.movePosition(cursor.Start)
                cursor.movePosition(cursor.Down, cursor.KeepAnchor,
                                    self.log_view.blockCount() - 2000)
                cursor.removeSelectedText()
            self._scroll_log()
        except Exception:
            pass

    def append_log(self, text):
        self.log_view.appendPlainText(text)
        self._scroll_log()

    def _scroll_log(self):
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ----- запуск Гордона -----
    def start_gordon(self):
        if self.runner and self.runner.isRunning():
            return
        self.runner = GordonRunner(GORDON, "Гордон (штатный прогон)")
        self.runner.log_line.connect(self.append_log)
        self.runner.finished.connect(self.on_runner_finished)
        self.runner.start()
        self.status_ind.set_active(True, "Гордон работает…")
        self.btn_start.setEnabled(False)
        self.btn_force.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def start_forced(self):
        if self.runner and self.runner.isRunning():
            QMessageBox.information(
                self, "Занято", "Гордон уже запущен. Сначала нажмите Стоп.")
            return
        warn = QMessageBox.warning(
            self, "⚠ Форсированная отправка",
            "Это запустит send_now.py ВНЕ окна 9-21 и вне дневных лимитов.\n"
            "Гард Гордона будет снят. Продолжить?",
            QMessageBox.Yes | QMessageBox.No)
        if warn != QMessageBox.Yes:
            return
        self.runner = GordonRunner(SEND_NOW, "Форсированная отправка (вне окна)")
        self.runner.log_line.connect(self.append_log)
        self.runner.finished.connect(self.on_runner_finished)
        self.runner.start()
        self.status_ind.set_active(True, "Форсированная отправка…")
        self.btn_start.setEnabled(False)
        self.btn_force.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def stop_gordon(self):
        if self.runner and self.runner.isRunning():
            self.append_log("[APP] Остановка Гордона…")
            self.runner.stop()

    def on_runner_finished(self, code):
        self.status_ind.set_active(False, "Гордон не запущен")
        # разблокируем ВСЕ кнопки запуска (работал любой из процессов)
        self.btn_start.setEnabled(True)
        self.btn_force.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_parse.setEnabled(True)
        self.btn_replies.setEnabled(True)
        # перечитываем всё по горячим следам
        self.refresh_all()

    # ----- запуск других агентов -----
    def _run_agent_script(self, script, label):
        """Запускает произвольный агентский скрипт в отдельном потоке.
        Если уже что-то крутится - новый запуск блокируем (один поток)."""
        if self.runner and self.runner.isRunning():
            QMessageBox.information(
                self, "Занято",
                "Сейчас уже работает процесс (Гордон / парсинг / просмотр).\n"
                "Дождитесь завершения или нажмите Стоп.")
            return
        self.runner = GordonRunner(script, label)
        self.runner.log_line.connect(self.append_log)
        self.runner.finished.connect(self.on_runner_finished)
        self.runner.start()
        # блокируем все кнопки запуска, пока процесс крутится
        self.btn_start.setEnabled(False)
        self.btn_force.setEnabled(False)
        self.btn_parse.setEnabled(False)
        self.btn_replies.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.append_log(f"[APP] {label} запущен - следите за лентой ↓")

    def open_parse_menu(self):
        """Меню запуска парсинга сайтов."""
        menu = QMessageBox(self)
        menu.setWindowTitle("🕷 Парсинг сайтов")
        menu.setText(
            "agent_parser.py читает URL из gordon_sources.txt и вытаскивает\n"
            "email + Telegram, добавляя их в БД как pending.\n\n"
            "Что сделать?")
        menu.setStyleSheet(f"QMessageBox {{ background:{BG}; color:{TEXT}; }}")
        b_run = menu.addButton("▶ Запустить парсинг", QMessageBox.AcceptRole)
        b_add = menu.addButton("➕ Добавить URL в источники", QMessageBox.ActionRole)
        b_view = menu.addButton("📄 Показать источники", QMessageBox.ActionRole)
        b_cancel = menu.addButton("Отмена", QMessageBox.RejectRole)
        menu.exec()
        clicked = menu.clickedButton()
        if clicked == b_run:
            self._run_agent_script(
                os.path.join(HERE, "agent_parser.py"), "Парсинг сайтов")
        elif clicked == b_add:
            self._add_source_url()
        elif clicked == b_view:
            self._show_sources()

    def _add_source_url(self):
        text, ok = QInputDialog.getText(
            self, "Добавить URL в источники",
            "URL сайта (добавится в gordon_sources.txt):")
        if not ok or not text.strip():
            return
        url = text.strip()
        try:
            with open(os.path.join(HERE, "gordon_sources.txt"), "a",
                      encoding="utf-8") as f:
                f.write(url + "\n")
            self.append_log(f"[APP] URL добавлен в источники: {url}")
            QMessageBox.information(
                self, "Готово", f"Добавлено:\n{url}")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось записать: {e}")

    def _show_sources(self):
        try:
            with open(os.path.join(HERE, "gordon_sources.txt"),
                      "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f
                         if l.strip() and not l.strip().startswith("#")]
        except Exception as e:
            lines = [f"(ошибка чтения: {e})"]
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Источники парсинга ({len(lines)})")
        dlg.resize(560, 480)
        dlg.setStyleSheet(f"QDialog {{ background:{BG}; color:{TEXT}; }}")
        lay = QVBoxLayout(dlg)
        te = QPlainTextEdit()
        te.setReadOnly(True)
        te.setPlainText("\n".join(lines))
        te.setStyleSheet(
            f"background:{PANEL}; color:{TEXT}; border:1px solid #262b36; "
            f"border-radius:8px; font-family:Consolas;")
        lay.addWidget(te)
        ok = QPushButton("Закрыть")
        ok.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:#0f1115; font-weight:bold; "
            f"border-radius:8px; padding:8px; }}")
        ok.clicked.connect(dlg.accept)
        lay.addWidget(ok)
        dlg.exec()

    def open_replies(self):
        """Окно просмотра ответов + кнопка проверки IMAP (agent_recorder.py)."""
        dlg = QDialog(self)
        dlg.setWindowTitle("📥 Ответы от людей")
        dlg.resize(640, 560)
        dlg.setStyleSheet(f"QDialog {{ background:{BG}; color:{TEXT}; }}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        info = QLabel(
            "Сверху - сайты со статусом replied / hired (кто ответил).\n"
            "Кнопка «Проверить почту» запускает agent_recorder.py, который "
            "поллит IMAP-ящики на входящие ответы и помечает их в БД.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        lay.addWidget(info)

        # таблица откликов
        tbl = QTableWidget()
        tbl.setColumnCount(5)
        tbl.setHorizontalHeaderLabels(["ID", "URL", "Email", "Status", "Notes"])
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setStyleSheet(self._table_style())
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        lay.addWidget(tbl, 1)

        def load_replies():
            try:
                conn = get_conn()
                rows = conn.execute(
                    "SELECT id, url, email, status, notes FROM sites "
                    "WHERE status IN ('replied','hired') ORDER BY updated_at DESC"
                ).fetchall()
                conn.close()
            except Exception:
                rows = []
            tbl.setRowCount(len(rows))
            for i, r in enumerate(rows):
                vals = [str(r["id"]), r["url"] or "", r["email"] or "",
                        r["status"], (r["notes"] or "")[:120]]
                for c, v in enumerate(vals):
                    item = QTableWidgetItem(str(v))
                    if c == 3:
                        col = STATUS_COLORS.get(r["status"], MUTED)
                        item.setBackground(QColor(col))
                        item.setForeground(QColor("#0f1115"))
                        item.setFont(QFont("Segoe UI", 9, QFont.Bold))
                    tbl.setItem(i, c, item)

        load_replies()

        # кнопки
        btns = QHBoxLayout()
        btns.setSpacing(8)
        b_check = QPushButton("📨 Проверить почту (IMAP)")
        b_check.setStyleSheet(
            f"QPushButton {{ background:#22c55e; color:#0f1115; font-weight:bold; "
            f"border-radius:8px; padding:8px; }}"
        )
        b_refresh = QPushButton("🔄 Обновить")
        b_refresh.setStyleSheet(
            f"QPushButton {{ background:{PANEL2}; color:{TEXT}; "
            f"border:1px solid #2c3340; border-radius:8px; padding:8px; }}"
        )
        b_close = QPushButton("Закрыть")
        b_close.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:#0f1115; font-weight:bold; "
            f"border-radius:8px; padding:8px; }}"
        )

        def do_check():
            self._run_agent_script(
                os.path.join(HERE, "agent_recorder.py"), "Проверка ответов (IMAP)")
            b_check.setEnabled(False)
            # после завершения - обновим таблицу и дашборд
            # (on_runner_finished дёрнет refresh_all; плюс таймеры сами освежат)

        b_check.clicked.connect(do_check)
        b_refresh.clicked.connect(load_replies)
        b_close.clicked.connect(dlg.accept)
        btns.addWidget(b_check)
        btns.addWidget(b_refresh)
        btns.addWidget(b_close)
        lay.addLayout(btns)

        dlg.exec()

    # ----- детали -----
    def open_details(self, index):
        row = index.row()
        id_item = self.table.item(row, 0)
        if not id_item:
            return
        sid = int(id_item.text())
        dlg = SiteDetailDialog(sid, self)
        dlg.exec()


# ---------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(
        f"QMainWindow {{ background:{BG}; color:{TEXT}; }}"
        f"QToolTip {{ background:#14122a; color:{TEXT}; border:1px solid {BORDER}; }}")
    win = GordonDesktop()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
