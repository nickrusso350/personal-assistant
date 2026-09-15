"""Unit tests for synthesize._distinct_times, through both of its consumers.

Ruled 2026-09-14, shipped in 1cc90a4: a time conflict is a disagreement about
the instant, not the clock string. Zoned members compare as UTC instants; a
zone-null (gmail) member agrees when its clock matches any zoned member's
clock and otherwise counts as distinct. _distinct_times is the single helper
behind derive_conflicts and filter_time_conflict, so every case asserts the
helper's count AND both consumers' output - they cannot drift apart unseen.

The four cases are the ones 1cc90a4 was verified against ("synthetic fixture
(4 cases)"). That script was not retained; these records are rebuilt from the
case descriptions recorded at the ruling. The date is fixed, not relative to
today, because the offsets under test depend on it: on 2026-09-14 New York and
Chicago are both on daylight time, so 19:30 in one is 18:30 in the other.

No I/O beyond the provenance header, no network, no state file access.
Importing synthesize imports the anthropic SDK; nothing here calls it.

Run: python3 test_distinct_times.py
"""

import os
import socket
import subprocess

from synthesize import _distinct_times, derive_conflicts, filter_time_conflict

DAY = "2026-09-14"
NY = "America/New_York"
CHI = "America/Chicago"


def provenance():
    """Instruments identify themselves (working rule, 2026-09-14)."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        head = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=here, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "(git unavailable)"
    print(f"host: {socket.gethostname()}")
    print(f"pwd:  {os.getcwd()}")
    print(f"head: {head}\n")


def record(clock, zone):
    """The three fields _distinct_times reads, in the shape build_records emits."""
    return {"date": DAY, "time": clock, "zone": zone}


def check(by_id, expected_count):
    """Assert the helper's count, then that both consumers agree with it."""
    members = list(by_id)
    timed = [m for m in members if by_id[m].get("time")]

    count = _distinct_times(timed, by_id)
    assert count == expected_count, (count, expected_count)

    derived = derive_conflicts(members, by_id)
    filtered = filter_time_conflict({"field": "time", "members": members}, by_id)
    if expected_count >= 2:
        expected = {"field": "time", "members": timed}
        assert derived == [expected], derived
        assert filtered == expected, filtered
    else:
        assert derived == [], derived
        assert filtered is None, filtered


def test_a_same_instant_two_zones_plus_matching_bare_clock():
    """One flight stored by two writers in two zones, plus a gmail commitment
    carrying the departure clock and no zone: one instant, no conflict. This
    is the 9/14 false positive the ruling retires."""
    by_id = {
        "r1": record("19:30", NY),
        "r2": record("18:30", CHI),
        "r3": record("19:30", None),
    }
    clocks = {r["time"] for r in by_id.values()}
    assert len(clocks) == 2, "fixture must disagree as clock strings, or it proves nothing"

    check(by_id, 1)
    print("  OK — a: 19:30 ET / 18:30 CT / bare 19:30 -> one instant, no conflict")


def test_b_real_disagreement_in_one_zone():
    """Two different instants in the same zone are a real conflict."""
    by_id = {
        "r1": record("19:30", NY),
        "r2": record("20:15", NY),
    }
    check(by_id, 2)
    print("  OK — b: 19:30 ET / 20:15 ET -> two instants, conflict on both")


def test_c_bare_clock_matching_nothing():
    """Case a with only the bare clock moved: it matches neither zoned clock,
    so it counts as its own time. A zone-null clock is unknown, never assumed
    to be local."""
    by_id = {
        "r1": record("19:30", NY),
        "r2": record("18:30", CHI),
        "r3": record("21:00", None),
    }
    check(by_id, 2)
    print("  OK — c: 19:30 ET / 18:30 CT / bare 21:00 -> two times, conflict")


def test_d_all_day_only():
    """All-day records carry no time and cannot disagree about one (ruled
    2026-09-04): nothing is counted, nothing is derived, the filter drops it."""
    by_id = {
        "r1": record(None, None),
        "r2": record(None, None),
    }
    check(by_id, 0)
    print("  OK — d: all-day only -> no times counted, no conflict")


def main():
    provenance()
    tests = [
        ("a. same instant, two zones, matching bare clock", test_a_same_instant_two_zones_plus_matching_bare_clock),
        ("b. real disagreement in one zone", test_b_real_disagreement_in_one_zone),
        ("c. bare clock matching nothing", test_c_bare_clock_matching_nothing),
        ("d. all-day only", test_d_all_day_only),
    ]
    for name, test in tests:
        print(name)
        test()
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
