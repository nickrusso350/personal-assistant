#!/usr/bin/env python3
"""Run the zone-of-render fixtures through synthesize(). Explicit invocation only.

Written 2026-09-25 for Option D (ruled 2026-09-24): synthesis proposes a
render_zone and end_render_zone per group, code admits them through ZoneInfo.
fixtures/zone_fixtures.json holds seven self-authored cases, their records in
the SYNTHESIS INPUT shape, and the expected membership and zones - all written
down before the first call was ever made. Cases f1 and f2 were added
2026-09-27 for fix shape (B): r24's twin and a non-home twin, whose zones come
from airports.py by construction.

Every pass first runs airports.self_check() (ruled 2026-09-27): a table zone
ZoneInfo cannot resolve fails the pass, and no API call is made for it. The
result line naming the check is part of what must be byte-identical.

Every pass is ONE API call over all seven cases as one morning. Without --live
this prints usage and exits: nothing here runs by accident, and
preview_digest.py's default path stays $0 and network-free. Touches no state,
no Reminders, no log file; writes nothing.

Checked: each expected group appears with exactly its members, and its two
zones equal the expected ones. Container and primary are not checked here.
Stability is the working rule: measured once is not measured. The per-pass
result lines must be byte-identical across every pass, and a pass that
disagrees with the others is a failure to report, not a thing to tune.

Usage: python3 zone_fixtures.py --live [--passes N]    (default 3)
"""
import contextlib
import datetime
import io
import json
import os
import socket
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

from airports import AIRPORT_ZONES, self_check                  # noqa: E402
from synthesize import synthesize                               # noqa: E402

FIXTURE = os.path.join(REPO, "fixtures", "zone_fixtures.json")


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


def one_pass(fixture):
    """One synthesize() call. Returns (result_lines, log_lines, all_ok).

    The SYNTHESIS lines go to a buffer, not the terminal; only ZONE and
    FALLBACK are kept, because those are what this instrument is about.
    """
    bad = self_check()
    if bad:
        return [f"airports self_check FAIL - unresolvable: {bad}"], [], False
    check_line = f"airports self_check ok ({len(AIRPORT_ZONES)} entries)"
    today = datetime.date.fromisoformat(fixture["today"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        grouping, cause = synthesize(fixture["records"], today)
    logs = [line for line in buf.getvalue().splitlines()
            if line.startswith(("SYNTHESIS ZONE", "SYNTHESIS FALLBACK"))]
    if grouping is None:
        return [check_line, f"FALLBACK cause={cause}"], logs, False
    by_members = {tuple(sorted(g["members"])): g for g in grouping["groups"]}
    lines, all_ok = [check_line], True
    for exp in fixture["expected"]:
        got = by_members.get(tuple(sorted(exp["members"])))
        if got is None:
            lines.append(f"({exp['case']}) MEMBERSHIP FAIL - no group with "
                         f"exactly {exp['members']}")
            all_ok = False
            continue
        zones = (got.get("render_zone"), got.get("end_render_zone"))
        want = (exp["render_zone"], exp["end_render_zone"])
        ok = zones == want
        all_ok &= ok
        lines.append(f"({exp['case']}) {'ok  ' if ok else 'FAIL'} "
                     f"got {zones[0]} -> {zones[1]}   "
                     f"expected {want[0]} -> {want[1]}")
    return lines, logs, all_ok


def main():
    args = sys.argv[1:]
    if "--live" not in args:
        sys.exit(__doc__.strip().splitlines()[-1])
    passes = 3
    if "--passes" in args:
        passes = int(args[args.index("--passes") + 1])
    provenance()
    with open(FIXTURE, encoding="utf-8") as handle:
        fixture = json.load(handle)
    runs = []
    for n in range(1, passes + 1):
        lines, logs, ok = one_pass(fixture)
        runs.append(lines)
        print(f"=== pass {n}  {'ALL EXPECTED' if ok else 'MISMATCH'} ===")
        for line in lines + logs:
            print("  " + line)
    stable = all(r == runs[0] for r in runs)
    print(f"\n{passes} passes: {'STABLE (byte-identical)' if stable else 'UNSTABLE'}")


main()
