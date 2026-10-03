#!/usr/bin/env python3
"""Run the primary-selector fixtures through synthesize(). Explicit invocation only.

Written 2026-10-03 for option (b) of the primary-drift design table (ruled
2026-10-02, amended 2026-10-03): the model proposes a primary, and
synthesize._select_primaries selects it by an exact ordered rule.
fixtures/primary_fixtures.json holds nine self-authored cases - the twin
shapes the ruling names, a hotel stay beside its check-in, a two-leg journey,
and two negatives - with the expected primary, the deciding term, and the
SYNTHESIS PRIMARY line expected (or its absence), all written down before the
first run. The file is its own because zone_fixtures.json carries no primary
and zone_fixtures.py checks zones only.

Two layers per pass:
  OFFLINE - the file's "proposals" are fed through synthesize() as the model's
    reply, with _call_model replaced by a stub, so the real post-parse path
    runs (parse, airports override, validate, selector, RESULT) on a primary
    this script chose. The PRIMARY line, or its silence, is checked exactly.
    $0 and network-free; this is the only layer without --live.
  LIVE (--live) - one real synthesize() call over all records as one morning.
    Checked: each expected group appears with exactly its members, and its
    final primary is the expected one; where a PRIMARY line names the group,
    its code pick and term must be the expected ones. Which member the model
    proposed is the drift this build exists to absorb, so it is reported but
    is not part of the result lines.

Stability is the working rule: measured once is not measured. The result
lines of every pass must be byte-identical; their md5 is printed per pass.
Touches no state, no Reminders, no log file; writes nothing.

Usage: python3 primary_fixtures.py [--live] [--passes N]    (default 3)
"""
import contextlib
import datetime
import hashlib
import io
import json
import os
import socket
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import synthesize as synth                                      # noqa: E402

FIXTURE = os.path.join(REPO, "fixtures", "primary_fixtures.json")
PREFIX = "SYNTHESIS PRIMARY "


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


def run(fixture, reply=None):
    """One synthesize() call; reply, when given, stands in for the model.
    Returns (grouping, cause, {sorted members tuple: PRIMARY payload},
    [SYNTHESIS FALLBACK lines])."""
    today = datetime.date.fromisoformat(fixture["today"])
    real = synth._call_model
    if reply is not None:
        synth._call_model = lambda prompt, log=True: (reply, "end_turn")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            grouping, cause = synth.synthesize(fixture["records"], today)
    finally:
        synth._call_model = real
    logs, fallbacks = {}, []
    for line in buf.getvalue().splitlines():
        if line.startswith(PREFIX):
            payload = json.loads(line[len(PREFIX):])
            logs[tuple(payload["members"])] = payload
        elif line.startswith("SYNTHESIS FALLBACK"):
            fallbacks.append(line)
    return grouping, cause, logs, fallbacks


def offline(fixture):
    """Result lines and all_ok for the stubbed-proposal layer."""
    groups = [{**p, "container": None, "render_zone": None,
               "end_render_zone": None} for p in fixture["proposals"]]
    grouping, cause, logs, _ = run(fixture, json.dumps({"groups": groups}))
    if grouping is None:
        return [f"OFFLINE FALLBACK cause={cause}"], False
    by_members = {tuple(sorted(g["members"])): g for g in grouping["groups"]}
    lines, all_ok = [], True
    for exp in fixture["expected"]:
        key = tuple(sorted(exp["members"]))
        got = by_members.get(key)
        log = logs.get(key)
        if got is None:
            lines.append(f"offline ({exp['case']}) MEMBERSHIP FAIL")
            all_ok = False
            continue
        want_log = exp["log"]
        got_log = (None if log is None else
                   {"model": log["model"], "code": log["code"], "term": log["term"]})
        ok = got["primary"] == exp["primary"] and got_log == want_log
        all_ok &= ok
        lines.append(f"offline ({exp['case']}) {'ok  ' if ok else 'FAIL'} "
                     f"primary {got['primary']} (expected {exp['primary']})  "
                     f"log {_show(got_log)} (expected {_show(want_log)})")
    unexpected = set(logs) - {tuple(sorted(e["members"])) for e in fixture["expected"]}
    if unexpected:
        lines.append(f"offline UNEXPECTED PRIMARY lines for {sorted(unexpected)}")
        all_ok = False
    return lines, all_ok


def live(fixture):
    """Result lines, report lines, and all_ok for one real call."""
    grouping, cause, logs, fallbacks = run(fixture)
    if grouping is None:
        return [f"live FALLBACK cause={cause}"], fallbacks, False
    by_members = {tuple(sorted(g["members"])): g for g in grouping["groups"]}
    lines, report, all_ok = [], [], True
    for exp in fixture["expected"]:
        key = tuple(sorted(exp["members"]))
        got = by_members.get(key)
        if got is None:
            lines.append(f"live ({exp['case']}) MEMBERSHIP FAIL - no group with "
                         f"exactly {exp['members']}")
            all_ok = False
            continue
        log = logs.get(key)
        ok = got["primary"] == exp["primary"]
        if log is not None:
            ok &= log["code"] == exp["primary"] and log["term"] == exp["term"]
        all_ok &= ok
        lines.append(f"live ({exp['case']}) {'ok  ' if ok else 'FAIL'} "
                     f"members {','.join(key)} primary {got['primary']} "
                     f"(expected {exp['primary']})")
        model = log["model"] if log is not None else got["primary"]
        report.append(f"live ({exp['case']}) model proposed {model}"
                      + (f", code overrode on term {log['term']}" if log else ""))
    return lines, report + fallbacks, all_ok


def _show(log):
    if log is None:
        return "silent"
    return f"model={log['model']} code={log['code']} term={log['term']}"


def main():
    args = sys.argv[1:]
    passes = 3
    if "--passes" in args:
        passes = int(args[args.index("--passes") + 1])
    is_live = "--live" in args
    provenance()
    with open(FIXTURE, encoding="utf-8") as handle:
        fixture = json.load(handle)
    runs = []
    for n in range(1, passes + 1):
        lines, ok = offline(fixture)
        report = []
        if is_live:
            live_lines, report, live_ok = live(fixture)
            lines, ok = lines + live_lines, ok and live_ok
        digest = hashlib.md5("\n".join(lines).encode("utf-8")).hexdigest()
        runs.append(lines)
        print(f"=== pass {n}  {'ALL EXPECTED' if ok else 'MISMATCH'}  md5 {digest} ===")
        for line in lines:
            print("  " + line)
        for line in report:
            print("  | " + line)
    stable = all(r == runs[0] for r in runs)
    print(f"\n{passes} passes: {'STABLE (byte-identical)' if stable else 'UNSTABLE'}")


main()
