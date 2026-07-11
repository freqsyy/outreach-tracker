#!/usr/bin/env python3
# bridge_poll.py - надёжный опрос KV inbox -> bridge_pending.txt
# Запускается из крон-промпта каждую минуту (вместо bridge_me.py).
# Не падает молча: всё в try/except, дедуп через bridge_done.txt.
import os
import sys
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_DONE = os.path.join(HERE, "bridge_done.txt")   # обработанные (история)
PENDING = os.path.join(HERE, "bridge_pending.txt")  # очередь (in-flight)
STATUS = os.path.join(HERE, "bridge_status.txt")
INBOX_KV = "inbox"
NS_DEFAULT = "77f5a72e4922438eab47b7547aaa746c"


def load_env():
    env = {}
    p = os.path.join(HERE, "bridge.env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def kv_get(env, key):
    ns = env.get("BRIDGE") or NS_DEFAULT
    url = ("https://api.cloudflare.com/client/v4/accounts/"
           f"{env['CF_ACCOUNT_ID']}/storage/kv/namespaces/{ns}/values/{key}")
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", "Bearer " + env["CF_API_TOKEN"])
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        return None
    except Exception:
        return None


def known_lines():
    # строки, которые уже в очереди ИЛИ уже обработаны - не дублируем
    known = set()
    for path in (PENDING, SEEN_DONE):
        if os.path.exists(path):
            for l in open(path, encoding="utf-8"):
                l = l.strip()
                if l:
                    known.add(l)
    return known


def main():
    try:
        env = load_env()
        if not all(k in env for k in ("CF_API_TOKEN", "CF_ACCOUNT_ID")):
            print("NO_ENV")
            return
        inbox = kv_get(env, INBOX_KV)
        if not inbox or not inbox.strip():
            print("EMPTY_INBOX")
            return
        lines = [l for l in inbox.strip().splitlines() if l.strip()]
        known = known_lines()
        new = [l for l in lines if l not in known]
        if not new:
            print("NO_NEW")
            return
        # дописываем в очередь (не затираем старые необработанные)
        with open(PENDING, "a", encoding="utf-8") as f:
            for l in new:
                f.write(l + "\n")
        # статус кириллицей - сразу, ещё до выполнения
        with open(STATUS, "w", encoding="utf-8") as f:
            f.write("Поймал задачу: " + new[-1][:60] + ". Делаю...\n")
        print("NEW_TASKS:" + str(len(new)))
        for l in new:
            print(l)
    except Exception as e:
        print("POLL_ERR:" + str(e))


if __name__ == "__main__":
    main()
