"""Rebuild fixtures/grouping_fixture1.json's records through build_records,
keeping the recorded groups. Read-only apart from the file it is told to
write, and it never calls the API.

Written 2026-09-16, at c313594. That commit added end_time and end_zone to
every record build_records writes; the fixture had been recorded before it,
so its records carried the older twelve keys. The recorded grouping is still
the model's answer to these records - capture_prompt.py proved the prompt
text byte-identical across that change - so only the record shape needed
refreshing, and the groups are carried through verbatim rather than
re-recorded. Re-recording would cost an API call and would replace the
model's recorded answer with a new one, which is a different thing entirely.

THE DATE SHIFT, and why it exists: preview_digest.py builds its fixtures
relative to date.today(), so rebuilding on any later day would stamp that
day's dates and move every record's "date" - a change a shape refresh must
not make. This reads the recorded departure date out of the old file, shifts
the fixture inputs back by the delta to it, and lets build_records reproduce
the recorded dates exactly. The comparison below is the proof the shift was
right: every key both files share must be equal, and the only keys the
rebuild may add are end_time and end_zone.

Usage: python3 refresh_fixture1.py <out_path>
       (to refresh in place: python3 refresh_fixture1.py fixtures/grouping_fixture1.json)
"""
import copy
import datetime
import json
import os
import socket
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.chdir(REPO)

import preview_digest as pv                                   # noqa: E402
from digest import partition_commitments                      # noqa: E402
from synthesize import build_records                          # noqa: E402

OLD = os.path.join(REPO, "fixtures", "grouping_fixture1.json")
ANCHOR_REF = ["event", "f1_evt_leg1", "single"]
ADDABLE = {"end_time", "end_zone"}


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


def shifted_inputs(delta):
    """preview_digest's fixture 1, moved back delta so build_records
    reproduces the recorded dates."""
    events = []
    for event in pv.F1_EVENTS:
        moved = copy.deepcopy(event)
        moved["start_local"] = event["start_local"] - delta
        moved["end_local"] = event["end_local"] - delta
        events.append(moved)
    commitments = []
    for commitment in pv.F1_COMMITMENTS:
        moved = copy.deepcopy(commitment)
        if moved.get("date"):
            moved["date"] = (
                datetime.date.fromisoformat(moved["date"]) - delta).isoformat()
        commitments.append(moved)
    return events, commitments


def compare(records, old_records):
    """Every failure the refresh must not commit. Returns a list of them."""
    failures = []
    if len(records) != len(old_records):
        failures.append(f"record count {len(records)} != {len(old_records)}")
    for new, was in zip(records, old_records):
        where = f"{new.get('id')}/{was.get('id')}"
        if new["id"] != was["id"]:
            failures.append(f"{where}: id mismatch")
        if list(new["ref"]) != list(was["ref"]):
            failures.append(f"{where}: ref {new['ref']} != {was['ref']}")
        added = set(new) - set(was)
        dropped = set(was) - set(new)
        # Subset, not equality: after the first refresh the old file already
        # carries the pair, and a second run must still pass unchanged.
        if not added <= ADDABLE:
            failures.append(f"{where}: unexpected new keys {sorted(added - ADDABLE)}")
        if dropped:
            failures.append(f"{where}: dropped keys {sorted(dropped)}")
        for key in was:
            if key != "ref" and new.get(key) != was.get(key):
                failures.append(
                    f"{where}: {key} {was.get(key)!r} -> {new.get(key)!r}")
    return failures


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python3 refresh_fixture1.py <out_path>")
    provenance()

    with open(OLD, encoding="utf-8") as handle:
        old = json.load(handle)
    old_records = old["records"]

    anchor = next(r for r in old_records if r["ref"] == ANCHOR_REF)
    recorded_depart = datetime.date.fromisoformat(anchor["date"])
    delta = datetime.timedelta(days=(pv.F1_DEPART - recorded_depart).days)
    print(f"recorded departure {recorded_depart}, fixture departure "
          f"{pv.F1_DEPART}, shifting inputs back {delta.days} days")

    events, commitments = shifted_inputs(delta)
    today = pv.TODAY - delta
    partition = partition_commitments({c["id"]: c for c in commitments}, today)
    records = build_records(events, *partition, today)

    failures = compare(records, old_records)
    gained = sum(1 for new, was in zip(records, old_records)
                 if set(new) - set(was))
    print(f"\nrecords: {len(records)} (old {len(old_records)})")
    print(f"ids one-to-one: "
          f"{[r['id'] for r in records] == [r['id'] for r in old_records]}")
    print(f"records gaining {', '.join(sorted(ADDABLE))}: {gained}")
    for r in records:
        if r.get("end_time") or r.get("end_zone"):
            print(f"  {r['id']} {tuple(r['ref'])[1]}: time {r['time']} "
                  f"{r['zone']} -> end_time {r['end_time']} {r['end_zone']}")

    if failures:
        print("\nFAILURES:")
        for line in failures:
            print(f"  {line}")
        sys.exit("refusing to write")

    print("\nPROOF OK: ids and refs one-to-one, every shared key equal, "
          "nothing dropped, nothing added but the end pair")

    out = {"records": records, "groups": old["groups"], "cause": old["cause"]}
    with open(sys.argv[1], "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1)
    print(f"groups carried through untouched: {len(out['groups'])} groups, "
          f"cause {out['cause']!r}")
    print(f"wrote {sys.argv[1]}")


main()
