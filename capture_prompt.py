"""Capture the exact text _prompt sends to the model. Read-only and $0.

Written 2026-09-15, at 7d60463, to prove that the PROMPT_FIELDS allowlist
(ruled 2026-09-14, "identity is code's domain") reproduced the previous
everything-but-ref field set byte for byte. Kept as the standing check for
any later change to what the model sees: capture, change, capture, diff.

preview_digest.py's default path never calls synthesize, so capturing a real
SYNTHESIS INPUT line would cost an API call. This builds records exactly the
way run_digest builds them - build_records over preview_digest.py's fixtures
- and calls _prompt directly. The API is never called, state is never read,
and nothing outside out_dir is written.

The field list it reports is read back out of the prompt it just wrote,
never recomputed here: a harness that reimplements the thing under test
proves nothing.

Usage: python3 capture_prompt.py <out_dir>
"""
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
from synthesize import build_records, _prompt                  # noqa: E402


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


def fields_of(prompt):
    """The field names present in the prompt's records block, in
    first-appearance order. Read back from the prompt, never recomputed."""
    body = prompt.split("Records:\n", 1)[1]
    records = json.loads(body)
    order = []
    for record in records:
        for key in record:
            if key not in order:
                order.append(key)
    return records, order


def capture(name, events, partition, out_dir):
    records = build_records(events, *partition, pv.TODAY)
    prompt = _prompt(records, pv.TODAY)
    path = os.path.join(out_dir, f"{name}.prompt.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(prompt)
    _sent, order = fields_of(prompt)
    built = []
    for record in records:
        for key in record:
            if key not in built:
                built.append(key)
    withheld = [k for k in built if k not in order]
    print(f"{name}: {len(records)} records, {len(prompt)} chars -> {path}")
    print(f"  fields sent ({len(order)}): {', '.join(order)}")
    print(f"  withheld ({len(withheld)}): {', '.join(withheld) or 'none'}")


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python3 capture_prompt.py <out_dir>")
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    provenance()
    capture("corpus", pv.EVENTS,
            partition_commitments(pv.STATE["commitments"], pv.TODAY), out_dir)
    capture("fixture1", pv.F1_EVENTS, pv.f1_partition(), out_dir)


main()
