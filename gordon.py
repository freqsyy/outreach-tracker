#!/usr/bin/env python3
"""
gordon.py — КОМАНДИР (центр управления армией агентов).

Гоняет трёх агентов по очереди и пишет общий лог.
Каждый агент — отдельный модуль, общается через track.py + БД.

Запуск вручную:   python gordon.py
Авто (Windows Task Scheduler):  python gordon.py   (каждые N минут)

Лог всех прогонов: gordon_run.log
"""

import os
import subprocess
import sys

import gordon_common as gc

HERE = os.path.dirname(os.path.abspath(__file__))


def run_agent(script):
    gc.log(f"=== ZAPUSK {script} ===", "GORDON")
    try:
        res = subprocess.run([sys.executable, script],
                             capture_output=True, text=True, timeout=300,
                             cwd=HERE)
        # суб-агенты сами пишут в gordon_run.log через gc.log;
        # здесь только дублируем в консоль, чтобы не плодить дубли в файле
        for line in (res.stdout + res.stderr).splitlines():
            if line.strip():
                print(line)
    except Exception as e:
        gc.log(f"AGENT UPEL V OSHIBKU {script}: {e}", "GORDON")


def main():
    gc.log("GORDON: nachalo cikla.", "GORDON")
    # Агенты запускаются в порядке конвейера
    for agent in ["agent_parser.py", "agent_sender.py", "agent_recorder.py", "agent_bounce.py"]:
        run_agent(agent)
    gc.log("GORDON: cikl zavershen.", "GORDON")


if __name__ == "__main__":
    main()
