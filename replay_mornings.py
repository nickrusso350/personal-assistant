#!/usr/bin/env python3
"""Replay recorded SYNTHESIS INPUTs through the current contract. Read-only.

Reads digest.out, re-runs synthesize() on each morning's recorded records,
and reports membership as ref-sets so groupings are comparable across days.
One API call per morning. Touches no state, writes no files.

Usage: python3 replay_mornings.py 2026-09-11 2026-09-12 2026-09-13
"""
import contextlib, io, json, os, sys, datetime
from synthesize import synthesize

LOG = os.path.expanduser("~/Library/Logs/personal_assistant/digest.out")


def load(dates):
    out, cur = {}, None
    for line in open(LOG, errors="replace"):
        line = line.rstrip("\n")
        if line.startswith("RUN START"):
            cur = line.split("RUN START", 1)[1].strip()[:10]
            continue
        if not line.startswith("SYNTHESIS "):
            continue
        kind, _, payload = line[len("SYNTHESIS "):].partition(" ")
        if cur not in dates or kind not in ("INPUT", "RESULT"):
            continue
        try:
            out.setdefault(cur, {})[kind] = json.loads(payload)
        except ValueError:
            pass
    return out


def refsets(groups, by_id):
    sets = []
    for g in groups or []:
        refs = tuple(sorted(str(by_id.get(m, m)) for m in g.get("members", [])))
        sets.append(refs)
    return sorted(sets)


def main():
    dates = sys.argv[1:]
    if not dates:
        sys.exit("give one or more dates")
    data = load(set(dates))
    for d in dates:
        entry = data.get(d)
        if not entry or "INPUT" not in entry:
            print("%s  NO INPUT" % d)
            continue
        recs = entry["INPUT"]["records"]
        today = datetime.date.fromisoformat(entry["INPUT"]["today"])
        by_id = {r["id"]: tuple(r["ref"]) for r in recs}
        old = refsets((entry.get("RESULT") or {}).get("groups"), by_id)
        with contextlib.redirect_stdout(io.StringIO()):
            groups, cause = synthesize(recs, today)
        if groups is None:
            print("%s  FALLBACK cause=%s" % (d, cause))
            continue
        new = refsets(groups["groups"], by_id)
        same = old == new
        print("=== %s  records=%d  old_groups=%d  new_groups=%d  %s ==="
              % (d, len(recs), len(old), len(new),
                 "UNCHANGED" if same else "CHANGED"))
        if not same:
            for s in new:
                if s not in old:
                    print("  NEW   %d members: %s" % (len(s), " | ".join(s)))
            for s in old:
                if s not in new:
                    print("  GONE  %d members: %s" % (len(s), " | ".join(s)))


main()
