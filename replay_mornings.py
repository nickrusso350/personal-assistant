#!/usr/bin/env python3
"""Replay recorded SYNTHESIS INPUTs through the current contract. Read-only.

Reads digest.out, re-runs synthesize() on each morning's recorded records,
and reports membership as ref-sets so groupings are comparable across days.
One API call per morning. Touches no state, writes no files.

Extended 2026-09-25 for zone-of-render (Option D, ruled 2026-09-24). Two
additions; everything above is unchanged, including the header line and the
NEW/GONE lines:
  PRIMARY - for each group whose membership matches the recorded RESULT, a
    line when the primary differs, and a count on its own line.
  G lines - every new group in canonical order (validate's, by lowest member
    id): member ids, primary, render_zone -> end_render_zone. Per-run ids
    only, no record text, so the lines of two passes can be diffed as they
    stand; byte-identical G lines across passes is the stability test.
SYNTHESIS ZONE lines (a key validate() nulled) are passed through from the
otherwise swallowed log.
SYNTHESIS FALLBACK lines are passed through the same way (2026-09-26): the
error text is what attributes a fallback, and the cause alone does not.
SYNTHESIS PRIMARY lines are passed through the same way (2026-10-03): with
code selecting the primary, the G lines show code's pick, and these lines
are where the model's differing proposal and the deciding term survive.

Usage: python3 replay_mornings.py 2026-09-11 2026-09-12 2026-09-13
"""
import contextlib, io, json, os, socket, subprocess, sys, datetime
from synthesize import synthesize

REPO = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.expanduser("~/Library/Logs/personal_assistant/digest.out")


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


def byrec(members):
    """Member ids in record order (r2 before r10), so a G line is a set, not
    the order the model happened to list members in."""
    return sorted(members, key=lambda m: int(m[1:]) if m[1:].isdigit() else 0)


def refsets(groups, by_id):
    sets = []
    for g in groups or []:
        refs = tuple(sorted(str(by_id.get(m, m)) for m in g.get("members", [])))
        sets.append(refs)
    return sorted(sets)


def main():
    provenance()
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
        old_groups = (entry.get("RESULT") or {}).get("groups")
        old = refsets(old_groups, by_id)
        swallowed = io.StringIO()
        with contextlib.redirect_stdout(swallowed):
            groups, cause = synthesize(recs, today)
        zone_lines = [l for l in swallowed.getvalue().splitlines()
                      if l.startswith("SYNTHESIS ZONE ")]
        fallback_lines = [l for l in swallowed.getvalue().splitlines()
                          if l.startswith("SYNTHESIS FALLBACK ")]
        primary_lines = [l for l in swallowed.getvalue().splitlines()
                         if l.startswith("SYNTHESIS PRIMARY ")]
        if groups is None:
            print("%s  FALLBACK cause=%s" % (d, cause))
            for line in zone_lines + fallback_lines:
                print("  " + line)
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
        old_primary = {tuple(sorted(g.get("members", []))): g.get("primary")
                       for g in old_groups or []}
        changed = 0
        for g in groups["groups"]:
            key = tuple(sorted(g["members"]))
            if key in old_primary and old_primary[key] != g["primary"]:
                changed += 1
                print("  PRIMARY %s: recorded %s  new %s"
                      % (byrec(g["members"])[0], old_primary[key], g["primary"]))
        print("  primary vs recorded: %s" % ("SAME" if not changed else "CHANGED %d" % changed))
        for g in groups["groups"]:
            print("  G %s primary=%s zones=%s -> %s"
                  % (",".join(byrec(g["members"])), g["primary"],
                     g.get("render_zone"), g.get("end_render_zone")))
        for line in zone_lines + primary_lines:
            print("  " + line)


main()
