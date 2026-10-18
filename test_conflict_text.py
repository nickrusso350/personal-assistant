"""Rendered-text tests for digest._conflict_text, dissenters only.

Ruled 2026-10-18: the "(sources disagree on time: ...)" parenthetical lists
dissenters only - members whose clock differs from the primary's
(group["primary"]) - never the primary's own time. A zoned member's clock is
converted to process-local with astimezone() and no argument and displayed
bare; clocks compare as "%H:%M" strings, display through format_time, and
sort ascending. One rule: the distinct local clocks of the timed members,
less the primary's own when the primary is timed; render if anything is left
(timed primary) or two or more remain (untimed primary); otherwise None. The
10/13 Owners Update line listed the primary's own 8:30 AM beside the 1:00 PM
that disagreed with it - case c is that group, rebuilt without summaries.

fixtures/conflict_text_fixtures.json is self-authored. Cases a-g call
_conflict_text directly on a group in the shape validate() returns; the
render case runs one calendar event and one gmail appointment through
build_digest to prove the call site in render_coming_up still appends the
text. Expected values were PINNED FROM A CAPTURED RUN, never typed before it;
a case with no "expected" key reports UNPINNED and fails.

The conversion reads the local zone from the interpreter, so main() pins it -
TZ=America/New_York and tzset(), restored in a finally - as
test_distinct_times.py does. tzset is imported by name: datetime.time is
already bound here.

No I/O beyond reading the fixture and the provenance header. No state file,
no network, no API. Importing digest imports the Google and Anthropic client
libraries; nothing here calls them.

Run: python3 test_conflict_text.py
"""

import json
import os
import socket
import subprocess
from datetime import date, datetime, time
from time import tzset
from zoneinfo import ZoneInfo

from digest import _conflict_text, build_digest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "conflict_text_fixtures.json")
PROCESS_LOCAL = "America/New_York"


def provenance():
    """Instruments identify themselves (working rule, 2026-09-14)."""
    try:
        head = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=HERE, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "(git unavailable)"
    print(f"host: {socket.gethostname()}")
    print(f"pwd:  {os.getcwd()}")
    print(f"head: {head}\n")


def aware(endpoint):
    """[date, clock, zone] -> the aware datetime fetch_calendar would return."""
    day, clock, zone = endpoint
    return datetime.combine(date.fromisoformat(day), time.fromisoformat(clock),
                            tzinfo=ZoneInfo(zone))


def unit(case):
    """_conflict_text on the case's group, by_rid built from its records."""
    by_rid = {r["id"]: r for r in case["records"]}
    return _conflict_text(case["group"], by_rid)


def rendered(case):
    """(day_header, line) pairs for every line carrying the case tag, from
    one build_digest call."""
    events = [{"id": e["id"], "summary": e["summary"], "all_day": e["all_day"],
               "start_local": aware(e["start"]), "end_local": aware(e["end"])}
              for e in case["events"]]
    state = {"commitments": {c["id"]: c for c in case["commitments"]}}
    grouping = {"records": case["records"], "groups": [case["group"]],
                "cause": None}
    _, body = build_digest(state, events, case["today"], grouping=grouping)
    header, hits = None, []
    for line in body.splitlines():
        if not line.startswith(" ") and line and line != "COMING UP":
            header = line
        elif case["tag"] in line:
            hits.append([header, line.strip()])
    return hits


def run(fixture):
    failures = 0
    for case in fixture["cases"]:
        got = unit(case)
        print(f"{case['case']}. {case['why']}")
        if "expected" not in case:
            failures += 1
            print(f"  UNPINNED — {got!r}")
        elif got != case["expected"]:
            failures += 1
            print(f"  FAIL — got {got!r}\n         want {case['expected']!r}")
        else:
            print(f"  OK — {got!r}")

    case = fixture["render"]
    hits = rendered(case)
    print(f"{case['case']}. {case['why']}")
    if len(hits) != 1:
        failures += 1
        print(f"  FAIL — {len(hits)} lines carry {case['tag']!r}: {hits}")
    elif "expected" not in case:
        failures += 1
        print(f"  UNPINNED — {hits[0][0]} | {hits[0][1]}")
    elif hits[0] != [case["expected"]["header"], case["expected"]["line"]]:
        failures += 1
        print(f"  FAIL — got {hits[0][0]} | {hits[0][1]}\n"
              f"         want {case['expected']['header']} | {case['expected']['line']}")
    else:
        print(f"  OK — {hits[0][0]} | {hits[0][1]}")
    return failures


def main():
    provenance()
    with open(FIXTURE, encoding="utf-8") as handle:
        fixture = json.load(handle)
    saved = os.environ.get("TZ")
    os.environ["TZ"] = PROCESS_LOCAL
    tzset()
    try:
        failures = run(fixture)
    finally:
        if saved is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = saved
        tzset()
    if failures:
        raise SystemExit(f"\n{failures} case(s) failed or unpinned.")
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
