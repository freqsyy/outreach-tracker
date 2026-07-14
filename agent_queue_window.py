#!/usr/bin/env python3
# agent_queue_window.py - маленькое окно "Очередь агентов" Гордона x6.
# ТОЛЬКО визуализация: кто какой агент, что в очереди lane (pending/claimed/done).
# НЕ запускает агентов, НЕ пишет в БД, НЕ шлёт письма - read-only + показ спецов.
# Запуск: python agent_queue_window.py  (двойной клик тоже ок, если Python в PATH)
import os
import tkinter as tk
from tkinter import ttk, messagebox

HOME = os.path.dirname(os.path.abspath(__file__))
DISPATCH = r"C:\Users\nazar\dispatch"
PROMPTS = r"C:\Users\nazar\agency-agents\personal-army"

# lane -> (имя, роль, файл промпта, папка lane в dispatch)
AGENTS = [
    ("01", "Scout",   "Lead Finder - ищет свежие сайты и лицы для рассылки",
          "01-scout.md",   "lane01"),
    ("02", "Gordon",  "Cold Outreach - шлёт холодные письма (5 акков, ротация)",
          "02-gordon.md",  "lane02"),
    ("03", "Herald",  "Соц-контент / посты / твиты (НЕ публикует, только черновики)",
          "03-herald.md",  "lane03"),
    ("04", "Hook",    "Офферы и креативы (Copy) - генерит хуки под аудиторию",
          "04-hook.md",    "lane04"),
    ("05", "Nurture", "Дрип и ре-engage - греет лицы, возвращает неответивших",
          "05-nurture.md", "lane05"),
    ("06", "Pitch",   "Closer - дочёркивает отвеченных лицов до сделки (DRAFT)",
          "06-pitch.md",   "lane06"),
    ("07", "Sage",    "Оркестратор HEAD - держит армию, один писатель в БД/git",
          "07-sage.md",    None),
]


def count_lane(lane):
    """Считает .md-задачи в pending/claimed/done для laneNN."""
    res = {"pending": 0, "claimed": 0, "done": 0}
    for state in res:
        d = os.path.join(DISPATCH, state, lane or "")
        if os.path.isdir(d):
            res[state] = sum(
                1 for f in os.listdir(d) if f.endswith(".md") and not f.endswith("idle.md")
            )
    return res


def load_prompt(fname):
    p = os.path.join(PROMPTS, fname)
    if os.path.isfile(p):
        return open(p, encoding="utf-8").read()
    return "(спец не найден: %s)" % p


class QueueWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Гордон x6 - Очередь агентов")
        self.geometry("760x460")
        self.configure(bg="#1e1e2e")

        # левая панель: список агентов
        left = tk.Frame(self, bg="#1e1e2e")
        left.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)
        tk.Label(left, text="АГЕНТЫ (lane)", bg="#1e1e2e", fg="#89b4fa",
                 font=("Consolas", 11, "bold")).pack(anchor="w")
        self.lb = tk.Listbox(left, width=26, height=14,
                              bg="#282838", fg="#cdd6f4", font=("Consolas", 10))
        self.lb.pack(fill=tk.Y, pady=4)
        self.lb.bind("<<ListboxSelect>>", self.on_select)
        tk.Button(left, text="Обновить очередь", command=self.refresh,
                  bg="#45475a", fg="#cdd6f4", relief=tk.FLAT).pack(fill=tk.X, pady=2)
        tk.Button(left, text="Спец агента (полный)", command=self.show_full,
                  bg="#45475a", fg="#cdd6f4", relief=tk.FLAT).pack(fill=tk.X, pady=2)

        # правая панель: детали
        right = tk.Frame(self, bg="#1e1e2e")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.title_var = tk.StringVar(value="Выбери агента слева")
        tk.Label(right, textvariable=self.title_var, bg="#1e1e2e", fg="#a6e3a1",
                 font=("Consolas", 12, "bold"), anchor="w").pack(fill=tk.X)
        self.info = tk.Text(right, bg="#282838", fg="#cdd6f4",
                            font=("Consolas", 10), wrap=tk.WORD, state=tk.DISABLED)
        self.info.pack(fill=tk.BOTH, expand=True)

        self.refresh()

    def refresh(self):
        self.lb.delete(0, tk.END)
        self.counts = {}
        for num, name, role, prompt, lane in AGENTS:
            c = count_lane(lane) if lane else {"pending": 0, "claimed": 0, "done": 0}
            self.counts[name] = c
            label = "%s %s  [p:%d c:%d d:%d]" % (num, name, c["pending"], c["claimed"], c["done"])
            self.lb.insert(tk.END, label)
        if self.lb.size():
            self.lb.selection_set(0)
            self.on_select(None)

    def on_select(self, _evt):
        idx = self.lb.curselection()
        if not idx:
            return
        num, name, role, prompt, lane = AGENTS[idx[0]]
        c = self.counts.get(name, {})
        self.title_var.set("%s %s" % (num, name))
        txt = (
            "РОЛЬ: %s\n\n"
            "ОЧЕРЕДЬ (dispatch/%s):\n"
            "  pending : %d\n  claimed : %d\n  done    : %d\n\n"
            "Что делает:\n%s\n\n"
            "[Кнопка 'Спец агента' - полный промпт]\n"
            "Окно ТОЛЬКО показывает. Запуск агента = красная зона,\n"
            "требует твоей команды (НЕ делаю сам)."
        ) % (role, lane or "(lane07 = HEAD, нет папки)",
              c.get("pending", 0), c.get("claimed", 0), c.get("done", 0), role)
        self._set_info(txt)

    def show_full(self):
        idx = self.lb.curselection()
        if not idx:
            return
        num, name, role, prompt, lane = AGENTS[idx[0]]
        self.title_var.set("%s %s - полный спец" % (num, name))
        self._set_info(load_prompt(prompt))

    def _set_info(self, text):
        self.info.configure(state=tk.NORMAL)
        self.info.delete("1.0", tk.END)
        self.info.insert("1.0", text)
        self.info.configure(state=tk.DISABLED)


if __name__ == "__main__":
    QueueWindow().mainloop()
