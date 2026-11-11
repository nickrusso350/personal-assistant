#!/usr/bin/env python3
"""Run wavetops.select() over fixtures/wavetops_fixtures.json.

Written 2026-11-07 for the wave-tops selection rule (ruled 2026-10-30;
file-shape rulings 2026-11-07). Self-authored fixtures only; this script
never opens the real groupings directory, touches no state, sends nothing,
and writes nothing.

Default mode prints, per case, the selected groups by primary id in order,
the evidence groups by primary id (select() returns (chosen, evidence) since
2026-11-11), and the WAVETOPS SELECT line, plus an md5 over the result lines.
It also prints no_flight_trip(grouping, run_date, chosen) per case as
"trip" (a [destination, first_date, last_date] list, or None). Each case's
"expected" (selected primaries, evidence primaries, trip, and log lines) is
checked exactly - a pin missing "evidence" or "trip" fails; a mismatch
prints FAIL, and a case with no "expected" prints UNPINNED and also counts as
a failure, so a case added without a pin cannot pass silently. Any failure
exits non-zero. Expectations were pinned 2026-11-07 from a captured run Nick
read, never typed ahead of it.

--stub runs the 10/20 stub check: every clause test is replaced so it admits
everything (_is_travel -> True, _is_task -> False, _has_place -> True,
_is_lodging -> False) and
each case's stubbed selection is compared with its real one. Until the
expectations are pinned, "FAIL under stub" means the stubbed selection
differs from the real one; a case that stays the same under the stub is one
whose result the clause tests do not decide.

Usage: python3 test_wavetops.py [--stub]
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

import wavetops                                                 # noqa: E402

FIXTURE = os.path.join(REPO, "fixtures", "wavetops_fixtures.json")


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


def run(case):
    """(selected primary ids, evidence primary ids, trip, captured log lines)
    for one case. select() returns (chosen, evidence) since 2026-11-11; trip
    is no_flight_trip(grouping, run_date, chosen) as a list, or None."""
    out = io.StringIO()
    run_date = datetime.date.fromisoformat(case["run_date"])
    with contextlib.redirect_stdout(out):
        chosen, evidence = wavetops.select(case["grouping"], run_date)
        trip = wavetops.no_flight_trip(case["grouping"], run_date, chosen)
    return ([g["primary"] for g in chosen], [g["primary"] for g in evidence],
            None if trip is None else list(trip), out.getvalue().splitlines())


@contextlib.contextmanager
def admit_everything():
    saved = (wavetops._is_travel, wavetops._is_task, wavetops._has_place,
             wavetops._is_lodging)
    wavetops._is_travel = lambda group, by_id: True
    wavetops._is_task = lambda primary: False
    wavetops._has_place = lambda primary: True
    wavetops._is_lodging = lambda record: False
    try:
        yield
    finally:
        (wavetops._is_travel, wavetops._is_task, wavetops._has_place,
         wavetops._is_lodging) = saved


def main():
    provenance()
    cases = json.load(open(FIXTURE))["cases"]
    stub = "--stub" in sys.argv[1:]
    results, failures = [], 0
    for case in cases:
        selected, evidence, trip, lines = run(case)
        if stub:
            with admit_everything():
                stubbed, _, _, stub_lines = run(case)
            verdict = "SAME under stub" if stubbed == selected else "FAIL under stub"
            print(f"{case['name']}: {verdict}")
            print(f"    real: {selected}  {lines}")
            print(f"    stub: {stubbed}  {stub_lines}")
            continue
        result = (f"{case['name']}: {selected} | evidence {evidence} | "
                  f"trip {trip} | {' / '.join(lines)}")
        results.append(result)
        want = case.get("expected")
        if want is None:
            status = "UNPINNED"
            failures += 1
        elif want == {"selected": selected, "evidence": evidence, "trip": trip,
                      "log": lines}:
            status = "PASS"
        else:
            status = "FAIL"
            failures += 1
        print(f"{status} {result}")
        print(f"    clause: {case['clause']}")
    if stub:
        return
    digest = hashlib.md5("\n".join(results).encode()).hexdigest()
    print(f"\nresult md5: {digest}")
    if failures:
        raise SystemExit(f"{failures} case(s) failed.")


if __name__ == "__main__":
    main()
