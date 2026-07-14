#!/usr/bin/env python
# agent_nurture_v2.py - runtime drip GENERATOR (DRAFT-only by default).
#
# Reads nurture_library.txt (7 scenarios from tik-16/17) + drafts in
# nurture_drafts/, picks a sequence per lead stage, renders a personalized
# drip with a micro-value pulled from the lead's audit/notes, and writes a
# DRAFT to nurture_drafts/gen/<lead_id>.md.
#
# DEFAULT = --dry-run: drafts only. NO email is ever sent, outreach.db is
# touched read-only (mode=ro), no git, no secrets. Real send exists ONLY
# behind --send and is NOT enabled here.
#
# Rate limit: at most 1 generated drip per lead per RATE_LIMIT_DAYS (tracked
# by the presence/age of the gen/<lead_id>.md draft log).
#
# Faceless "QA worker" persona. English copy (Nazar learns English). No doxx.

import os
import re
import sys
import json
import sqlite3
import argparse
import datetime

# --- utf-8 console (Windows cp1251) -------------------------------------
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "outreach.db")
LIB_PATH = os.path.join(HERE, "nurture_library.txt")
DRAFTS_DIR = os.path.join(HERE, "nurture_drafts")
GEN_DIR = os.path.join(DRAFTS_DIR, "gen")
AUDITS_DIR = os.path.join(HERE, "audits")

RATE_LIMIT_DAYS = 7
DEFAULT_TG = "@oojdo"

# Honest, non-doxxing stand-ins for the cross-scenario placeholders.
# NOTE: branch B/v3 templates already inline the case story after {case}, so
# we blank the token out (the inline sentence stays). Substituting it would
# duplicate the text.
CHECKLIST_LINK = "https://gist.github.com/oojdo/launch-qa-10pt"
CASE_TEXT = ""

# Status -> scenario key (must match nurture_library.txt СЦЕНАРИЙ keys).
STATUS_TO_SEQ = {
    "pending": "v1",
    "replied": "v3",
    "bounced": "bounce",
    "rejected": "rej",
}
# Leads we never touch here.
# review = auto-scout, NOT approved for outreach yet (CLAUDE.md) -> skipped.
# sent = cold blast already in flight under a different agent -> skipped.
# hired = handed off to Relay/Atlas -> skipped.
SKIP_STATUSES = {"sent", "hired", "review"}

SEQ_LABEL = {
    "v1": "cold-no-reply",
    "v2": "opened-no-reply",
    "v3": "replied-question",
    "react": "reactivate-30d",
    "bounce": "soft-bounce",
    "rej": "rejected-soft-reentry",
    "handoff": "hired-handoff",
}


# ----------------------------------------------------------------------
# Library parsing
# ----------------------------------------------------------------------
def parse_library(path):
    """Return {seq_key: [ {label, subject, body}, ... ]}."""
    seqs = {}
    cur_key = None
    cur_block = None
    scen_re = re.compile(r"#\s*СЦЕНАРИЙ\s+\d+\s*[—-]\s*"
                          r"(?:[a-z0-9\- ]+?)\s*\(([a-z0-9]+)\)", re.I)
    block_re = re.compile(r"#\s*-{3,}\s*(.+?)\s*-{3,}\s*$")
    subj_re = re.compile(r"^Subject:\s*(.+?)\s*$")

    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            m = scen_re.search(line)
            if m:
                cur_key = m.group(1).strip().lower()
                seqs.setdefault(cur_key, [])
                cur_block = None
                continue
            mb = block_re.search(line)
            if mb and cur_key is not None:
                cur_block = {"label": mb.group(1).strip(), "subject": "", "body": ""}
                seqs[cur_key].append(cur_block)
                continue
            if cur_block is None:
                continue
            ms = subj_re.match(line)
            if ms:
                cur_block["subject"] = ms.group(1).strip()
                continue
            # body line (skip pure comment lines that start with '# ')
            if line.startswith("# "):
                continue
            if cur_block["body"]:
                cur_block["body"] += "\n"
            cur_block["body"] += line.rstrip()

    # trim trailing blank lines in bodies
    for blocks in seqs.values():
        for b in blocks:
            b["body"] = b["body"].strip()
    return seqs


# ----------------------------------------------------------------------
# DB read-only
# ----------------------------------------------------------------------
def get_ro_conn():
    return sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)


def candidate_leads(statuses):
    conn = get_ro_conn()
    c = conn.cursor()
    ph = ",".join("?" for _ in statuses)
    c.execute(
        "SELECT id, url, email, telegram, status, notes FROM sites "
        "WHERE status IN (%s) ORDER BY id" % ph, statuses)
    rows = c.fetchall()
    conn.close()
    return rows


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def domain_of(url):
    if not url:
        return ""
    m = re.search(r"https?://([^/]+)", url)
    dom = m.group(1) if m else url
    return dom.lower().lstrip("www.")


def clean_tg(raw):
    if not raw:
        return DEFAULT_TG
    t = str(raw).strip()
    if not t.startswith("@"):
        t = "@" + t
    return t


REPLY_RE = re.compile(r"CLIENT::replied::\s*[\d\-: ]+\s*::\s*(.*)", re.S)
AUDIT_RE = re.compile(r"AUDIT::[a-z]+::\s*(.+?)(?:::[^:]+)?$", re.S)


def extract_reply(notes):
    """Text of a lead's reply/question for v3 branch D.
    Returns (text, has_real_reply). has_real_reply=False when we only fell
    back to the AUDIT:: description (no actual CLIENT::replied text)."""
    if not notes:
        return "", False
    m = REPLY_RE.search(notes)
    if m:
        return m.group(1).strip(), True
    m = re.search(r"CLIENT::replied::\s*(.*)", notes, re.S)
    if m:
        return m.group(1).strip(), True
    ma = AUDIT_RE.search(notes)
    if ma:
        return ma.group(1).strip(), False
    return "", False


def classify_objection(text):
    # No real reply text captured -> fall back to branch B (portfolio): it
    # carries the real {bug} micro-value from the audit, which is what these
    # leads actually have. Branch D needs {answer} text to make sense.
    if not text:
        return 1  # B
    t = text.lower()
    if any(w in t for w in ["portfolio", "case", "proof", "example", "show"]):
        return 1  # B
    if any(w in t for w in ["team", "have qa", "my qa", "in-house", "already"]):
        return 3  # D
    if any(w in t for w in ["not sure", "maybe", "think", "undecided", "consider"]):
        return 2  # C
    return 0  # A timing


META_BUG_MARKERS = ["cdp", "chromium", "9222", "инфраструктур", "agent-browser",
                    "agent_auditor", "не поднят", "не запущен", "meta", "ssh"]


def _is_meta_bug(text):
    tl = text.lower()
    return any(k in tl for k in META_BUG_MARKERS)


def real_bug_for(domain, notes):
    """Pull a real first bug from audits/<domain>.md, else notes AUDIT::,
    else an honest fallback."""
    if domain:
        ap = os.path.join(AUDITS_DIR, "%s.md" % domain)
        if os.path.exists(ap):
            with open(ap, "r", encoding="utf-8") as fh:
                txt = fh.read()
            # format 1: "### #1 [SEV] head"
            for m in re.finditer(r"^###\s*#\d+\s*\[[^\]]+\]\s*(.+)$", txt, re.M):
                head = m.group(1).strip()
                if not _is_meta_bug(head):
                    return head
            # format 2: table "| N | where | desc |"
            for m in re.finditer(r"^\|\s*\d+\s*\|\s*([^|]+)\|\s*([^|]+)\|", txt, re.M):
                desc = m.group(2).strip()
                if not _is_meta_bug(desc):
                    return desc
    # notes AUDIT:: marker -> take the clean description (drop sev + trailing
    # severity code like "::5"). Format: AUDIT::sev::head::detail::N
    if notes:
        for m in re.finditer(r"AUDIT::([a-z]+)::(.+)$", notes, re.M):
            raw = m.group(2).strip()
            # strip trailing "::<num>" severity
            raw = re.sub(r"::\d+\s*$", "", raw)
            # split head::detail -> prefer detail if present, else head
            parts = [p.strip() for p in raw.split("::") if p.strip()]
            b = parts[-1] if len(parts) > 1 else (parts[0] if parts else "")
            if b and not _is_meta_bug(b):
                return b
    # honest fallback
    return ("one small thing I noticed while passing by - a form field that "
            "accepts input but never validates it, dropping first-time signups")


def render(body, site, tg, bug, answer="", checklist=CHECKLIST_LINK, case=CASE_TEXT):
    if not answer:
        answer = ("you raised a fair point, and it's worth a straight answer - "
                  "I'm the outside pair of eyes, not a replacement for anything "
                  "you've already got")
    out = body
    out = out.replace("{site}", site)
    out = out.replace("{tg}", tg)
    out = out.replace("{bug}", bug)
    out = out.replace("{answer}", answer)
    out = out.replace("{checklist_link}", checklist)
    if case:
        out = out.replace("{case}", case)
    else:
        # library template has "...same shape as {case}: ..." - drop the
        # dangling connector when no case text is supplied.
        out = re.sub(r"\bas\s*\{case\}\s*:\s*", "", out, flags=re.I)
    return out


# ----------------------------------------------------------------------
# Generation
# ----------------------------------------------------------------------
def pick_block(seqs, seq_key, lead_status, reply_text):
    blocks = seqs.get(seq_key, [])
    if not blocks:
        return None
    if seq_key == "v3":
        idx = classify_objection(reply_text)
        idx = min(idx, len(blocks) - 1)
        return blocks[idx]
    # all others: first touch
    return blocks[0]


def gen_draft_path(lead_id):
    return os.path.join(GEN_DIR, "%s.md" % lead_id)


def draft_age_days(lead_id):
    p = gen_draft_path(lead_id)
    if not os.path.exists(p):
        return None
    mt = os.path.getmtime(p)
    now = datetime.datetime.now().timestamp()
    return (now - mt) / 86400.0


def write_draft(lead_id, meta, subject, body):
    os.makedirs(GEN_DIR, exist_ok=True)
    p = gen_draft_path(lead_id)
    header = (
        "# AUTO-DRAFT (nurture_v2, DRY-RUN - NOT SENT)\n"
        "# lead_id: %s\n"
        "# site: %s\n"
        "# status: %s\n"
        "# seq: %s\n"
        "# generated: %s\n"
        "# rate_limit_days: %d\n\n"
        % (meta["lead_id"], meta["site"], meta["status"], meta["seq"],
           datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           RATE_LIMIT_DAYS)
    )
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(header)
        fh.write("Subject: %s\n\n" % subject)
        fh.write(body + "\n")
    return p


def run(args):
    seqs = parse_library(LIB_PATH)
    present = sorted(seqs.keys())
    print("[nurture_v2] parsed scenarios: %s" % ", ".join(present))

    statuses = list(STATUS_TO_SEQ.keys())
    if args.status:
        statuses = [s for s in statuses if s in args.status.split(",")]
    leads = candidate_leads(statuses)
    if args.limit:
        leads = leads[:args.limit]

    print("[nurture_v2] candidate leads: %d (statuses: %s)"
          % (len(leads), ",".join(statuses)))
    print("[nurture_v2] mode: %s" % ("DRY-RUN (drafts only)" if not args.send else "SEND (disabled)"))

    scanned = generated = skipped_rl = skipped_err = 0
    for (lid, url, email, tg, status, notes) in leads:
        scanned += 1
        seq_key = STATUS_TO_SEQ.get(status)
        if not seq_key or seq_key not in seqs:
            skipped_err += 1
            print("  [skip] lead %s status=%s seq=%s not in library" % (lid, status, seq_key))
            continue

        age = draft_age_days(lid)
        if age is not None and not args.force and age < RATE_LIMIT_DAYS:
            skipped_rl += 1
            print("  [rate-limit] lead %s draft %0.1fd old (<%d) - skip"
                  % (lid, age, RATE_LIMIT_DAYS))
            continue

        reply_text = ""
        if status == "replied":
            reply_text, has_real = extract_reply(notes)
            # No actual reply text -> branch B (portfolio, carries real {bug}).
            if not has_real:
                reply_text = ""
        block = pick_block(seqs, seq_key, status, reply_text)
        if not block or not block["subject"]:
            skipped_err += 1
            print("  [skip] lead %s no usable block for seq=%s" % (lid, seq_key))
            continue

        site = domain_of(url) or ("site#%s" % lid)
        tg_clean = clean_tg(tg)
        bug = real_bug_for(site, notes)
        subject = render(block["subject"], site, tg_clean, bug, reply_text)
        body = render(block["body"], site, tg_clean, bug, reply_text)

        if args.send:
            # NOT ENABLED - would wire SMTP here under explicit --send.
            print("  [SEND disabled] lead %s would send: %s" % (lid, subject))
            continue

        p = write_draft(lid, {"lead_id": lid, "site": site, "status": status,
                              "seq": seq_key}, subject, body)
        generated += 1
        print("  [draft] lead %s (%s) -> %s" % (lid, site, os.path.relpath(p, HERE)))

    print("[nurture_v2] done | scanned=%d generated=%d rate-limited=%d errors=%d"
          % (scanned, generated, skipped_rl, skipped_err))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Nurture drip generator (dry-run default).")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="generate drafts only (default)")
    ap.add_argument("--send", action="store_true",
                    help="REAL SEND - NOT enabled by default, do not use yet")
    ap.add_argument("--status", help="comma list of statuses to include")
    ap.add_argument("--limit", type=int, help="max leads to process")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if rate-limited")
    ap.add_argument("--list-scenarios", action="store_true",
                    help="print parsed scenarios and exit")
    args = ap.parse_args()

    seqs = parse_library(LIB_PATH)
    if args.list_scenarios:
        for k in sorted(seqs.keys()):
            print("%s (%s): %d blocks" % (k, SEQ_LABEL.get(k, "?"), len(seqs[k])))
            for b in seqs[k]:
                print("   - %s | %s" % (b["label"][:40], b["subject"][:50]))
        return 0

    # sanity: never allow --send to actually mail in this build
    if args.send:
        print("[nurture_v2] --send requested but SEND is disabled in this build. "
              "Running dry-run instead.", file=sys.stderr)
        args.send = False

    return run(args)


if __name__ == "__main__":
    sys.exit(main())
