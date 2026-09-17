#!/usr/bin/env python3
"""Masked paste helper for SYNTHESIS log lines. Read-only.

Prints the INPUT/RESULT pair for a chosen morning with identifiers masked,
description_snippet dropped, and location truncated - the sanctioned way to
bring a morning into a chat session. Identifier masking is stable within a
dump (same value always gets the same token, so shared-identifier evidence
survives) and meaningless across dumps (sequential by first appearance).

end_time and end_zone are shown unmasked (ruled 2026-09-17): a clock string
and an IANA zone name carry nothing to mask, and they are exactly what a
conflict diagnosis needs to read. They joined KEEP on the same date; before
that they fell through to the unexpected-keys NOTE, which is the tripwire
working - a field added to a record must be classified deliberately.

Usage:  python3 synth_dump.py            list available dates
        python3 synth_dump.py 2026-09-12 dump that morning
"""
import json
import os
import re
import socket
import subprocess
import sys
from collections import OrderedDict

REPO = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.expanduser("~/Library/Logs/personal_assistant/digest.out")
PREFIX = "SYNTHESIS "
KEEP = ["id", "kind", "source", "date", "time", "zone", "end_time", "end_zone",
        "end_date", "summary", "location", "identifiers"]
DROP = ["description_snippet", "ref"]

LONGNUM = re.compile(r"\d{5,}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def provenance():
    """Instruments identify themselves (working rule, 2026-09-14)."""
    try:
        head = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "(git unavailable)"
    print(f"host: {socket.gethostname()}")
    print(f"pwd:  {os.getcwd()}")
    print(f"head: {head}\n")


def load_pairs():
    pairs = OrderedDict()
    current = None
    with open(LOG, "r", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("RUN START"):
                stamp = line.split("RUN START", 1)[1].strip()
                current = stamp[:10]
                continue
            if not line.startswith(PREFIX):
                continue
            kind, _, payload = line[len(PREFIX):].partition(" ")
            try:
                obj = json.loads(payload)
            except ValueError:
                continue
            pairs.setdefault(current, {})[kind] = obj
    return pairs


class Masker:
    def __init__(self):
        self.map = OrderedDict()

    def token(self, value):
        if value in self.map:
            return self.map[value]
        fam = "FLIGHT" if str(value).upper().startswith("FLIGHT") else "ID"
        n = sum(1 for v in self.map.values() if v.startswith(fam + "-"))
        label = "%s-%s" % (fam, chr(65 + n) if n < 26 else str(n))
        self.map[value] = label
        return label

    def scrub(self, text, limit=90):
        if not text:
            return ""
        text = " ".join(str(text).split())
        for raw, tok in self.map.items():
            bare = str(raw).replace("FLIGHT ", "")
            if len(bare) >= 4:
                text = text.replace(bare, tok)
        text = EMAIL.sub("<email>", text)
        text = PHONE.sub("<phone>", text)
        text = LONGNUM.sub("<num>", text)
        return text if len(text) <= limit else text[:limit] + "..."


def records_of(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("records", "input", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def main():
    provenance()
    if not os.path.exists(LOG):
        print("no log at " + LOG)
        return
    pairs = load_pairs()
    dates = [d for d in pairs if d]
    if len(sys.argv) < 2:
        print("available mornings (most recent last):")
        for d in dates:
            have = ",".join(sorted(pairs[d].keys()))
            print("  %s  [%s]" % (d, have))
        return
    want = sys.argv[1]
    if want not in pairs:
        print("no entry for " + want)
        return
    entry = pairs[want]
    mask = Masker()
    recs = records_of(entry.get("INPUT"))
    for r in recs:
        for ident in r.get("identifiers") or []:
            mask.token(ident)
    print("=== SYNTHESIS INPUT (masked) %s  %d records ===" % (want, len(recs)))
    for r in recs:
        out = OrderedDict()
        for k in KEEP:
            v = r.get(k)
            if k == "identifiers":
                v = [mask.token(i) for i in (v or [])]
            elif k in ("summary", "location"):
                v = mask.scrub(v, 90 if k == "summary" else 40)
            out[k] = v
        print(json.dumps(out))
    unseen = [k for r in recs for k in r if k not in KEEP and k not in DROP]
    if unseen:
        print("NOTE unexpected keys dropped: " + ",".join(sorted(set(unseen))))
    print()
    print("=== SYNTHESIS RESULT %s ===" % want)
    res = entry.get("RESULT")
    if res is None:
        print("(no RESULT line - synthesis failed or fell back)")
    else:
        groups = res.get("groups") if isinstance(res, dict) else res
        for g in groups or []:
            print(json.dumps(g))
    print()
    print("masked %d identifier values" % len(mask.map))


main()
