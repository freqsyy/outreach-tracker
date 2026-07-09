#!/usr/bin/env python3
"""Одноразовый откат лимитов Гордона после вечерней досылки (4 письма)."""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, ".env")

s = open(ENV, encoding="utf-8").read()
s = re.sub(r"^MAX_PER_RUN=.*", "MAX_PER_RUN=3", s, flags=re.M)
s = re.sub(r"^MAX_PER_DAY=.*", "MAX_PER_DAY=12", s, flags=re.M)
s = re.sub(r"^HOURS=.*", "HOURS=9-21", s, flags=re.M)
open(ENV, "w", encoding="utf-8").write(s)
print("Gordon .env reverted to MAX_PER_RUN=3, MAX_PER_DAY=12, HOURS=9-21")
