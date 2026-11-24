#!/usr/bin/env python3
"""Run wavetops.script() over fixtures/wavetops_script_fixtures.json.

Written 2026-11-23 for the wave-tops script (ruled 2026-11-23): code writes
every sentence from fixed patterns, no model call. Self-authored fixtures
only; this script never opens the real groupings directory, touches no
state, calls no API, sends nothing, and writes nothing.

Per case it prints the script's lines and the log lines script() printed
(WAVETOPS SELECT, DESTINATION DROPPED, ZONE UNKNOWN, CAP DROPPED, and
destinations' own DESTINATION UNKNOWN). Each case's "expected" ({"lines",
"log"}) is checked exactly; a mismatch prints FAIL, and a case with no
"expected" prints UNPINNED and also counts as a failure, so a case added
without a pin cannot pass silently. "line_cases" feed one fact-sheet row to
wavetops._line, for rows select() cannot reach today. Any failure exits
non-zero. An md5 over the result lines closes the run.

The fixtures' airport codes are invented and are in neither destination
table, so for the run destinations._overrides is replaced in memory by the
fixture file's "overrides" table and restored afterwards; the run prints
whether the real loader is back. No file is written.

Identity check: fact_sheet() finds a chosen group's file position by
id(), which holds because select() appends the group objects it iterates
(wavetops.select: "for index, group in enumerate(groups)" ... the same
`group` goes into travel/other) and never copies them. Every case asserts
it: each group select() returns `is` an element of grouping["groups"].

Usage: python3 test_wavetops_script.py [--fixture PATH]
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

import destinations                                             # noqa: E402
import wavetops                                                 # noqa: E402

FIXTURE = os.path.join(REPO, "fixtures", "wavetops_script_fixtures.json")


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


@contextlib.contextmanager
def fixture_destinations(table):
    """Swap destinations._overrides for an in-memory table; restore the real
    loader on the way out, whatever happened. Writes no file."""
    saved = destinations._overrides
    destinations._overrides = lambda: table
    try:
        yield saved
    finally:
        destinations._overrides = saved


def run(case):
    """(script lines, captured log lines, identity holds) for one case."""
    run_date = datetime.date.fromisoformat(case["run_date"])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        lines = wavetops.script(case["grouping"], run_date)
    with contextlib.redirect_stdout(io.StringIO()):
        chosen, evidence = wavetops.select(case["grouping"], run_date)
    groups = case["grouping"].get("groups") or []
    same = all(any(c is g for g in groups) for c in chosen + evidence)
    return lines, out.getvalue().splitlines(), same


def verdict(want, got):
    if want is None:
        return "UNPINNED"
    return "PASS" if want == got else "FAIL"


def main():
    provenance()
    args = sys.argv[1:]
    path = args[args.index("--fixture") + 1] if "--fixture" in args else FIXTURE
    doc = json.load(open(path))
    results, failures, identity = [], 0, 0
    with fixture_destinations(doc["overrides"]) as real:
        for case in doc["cases"]:
            lines, log, same = run(case)
            got = {"lines": lines, "log": log}
            status = verdict(case.get("expected"), got)
            if not same:
                status, identity = "FAIL", identity + 1
            failures += status != "PASS"
            results.append(f"{case['name']}: {json.dumps(got)}")
            print(f"{status} {case['name']}")
            for line in lines or ["(no script)"]:
                print(f"    | {line}")
            for line in log:
                print(f"    log: {line}")
            print(f"    clause: {case['clause']}")
        for case in doc["line_cases"]:
            got = wavetops._line(case["row"])
            status = verdict(case.get("expected"), got)
            failures += status != "PASS"
            results.append(f"line {case['name']}: {got}")
            print(f"{status} line {case['name']}")
            print(f"    | {got}")
            print(f"    clause: {case['clause']}")
    print(f"\ncases: {len(doc['cases'])}  line cases: {len(doc['line_cases'])}")
    print(f"identity failures: {identity}")
    print(f"real overrides loader restored: {destinations._overrides is real}")
    digest = hashlib.md5("\n".join(results).encode()).hexdigest()
    print(f"result md5: {digest}")
    if failures:
        raise SystemExit(f"{failures} case(s) failed.")


if __name__ == "__main__":
    main()
