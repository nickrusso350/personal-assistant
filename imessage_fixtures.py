#!/usr/bin/env python3
"""Run the iMessage extraction fixtures through extract_imessage. Explicit invocation only.

Written 2026-10-05 for the iMessage-as-source build (design table ruled
2026-10-04). fixtures/imessage_fixtures.json holds nineteen self-authored cases,
expected extractions written before the first run; this script reads it and
never edits it. All handles in it are invented.

Two layers:
  OFFLINE (default) - $0 and network-free. For every case and every target
    index: the prompt builds, carries the send-time anchor, and carries no raw
    handle. A "gate" target asserts its sender is outside the case's allowlist
    - the gate itself is a fetch-stage rule; this only checks the fixture is
    well-formed. Every expected extraction is run through apply_window_guard,
    and the outcome must match the case's after_guard ("drop" -> [], absent ->
    kept unchanged).
  LIVE (--live) - one extract_text_commitments call per non-gate target,
    context = the case's messages before the target index, then the guard.
    Case "g" also runs its extraction_negative (the spam text as if it had
    come through an allowlisted chat, expected []). Billable: one call each.

Pass criterion (ruled 2026-10-05): same length, and per item an exact match
on type, date, time and action_needed; "what" must be a non-empty string and
is printed, not graded. The guard outcome is graded on the after-guard list.
On FAIL the model's raw list is printed beside the expected one.

A case may list target indices under "ungraded" (ruled 2026-10-06, case i:1).
Live, an ungraded target still runs and prints UNGRADED with the model's list
and any guard line; it counts toward neither the exit code nor the PASS
total, and the summary shows the ungraded count separately. Offline, it is
checked as before.

One pass by design: the live layer runs once per invocation (brief,
2026-10-05). Touches no state, no Reminders, no log file; writes nothing.
Exit 0 only when every line passes.

Usage: python3 imessage_fixtures.py [--live]
"""
import re
import contextlib
import io
import json
import os
import socket
import subprocess
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import extract_imessage as xi                                    # noqa: E402

FIXTURE = os.path.join(REPO, "fixtures", "imessage_fixtures.json")
GRADED = ("type", "date", "time", "action_needed")


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


def _ts(message):
    return datetime.fromisoformat(message["ts"])


def guard(items, case, anchor_ts):
    """apply_window_guard with its log lines captured. Returns (kept, lines)."""
    days = case.get("guard_days", xi.GUARD_DAYS)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        kept = xi.apply_window_guard(items, anchor_ts, days)
    return kept, buf.getvalue().splitlines()


def expected_after_guard(case, index, expected):
    return [] if case.get("after_guard", {}).get(index) == "drop" else expected


WHAT_BANNED = frozenset(
    "monday tuesday wednesday thursday friday saturday sunday "
    "mon tue tues wed thu thur thurs fri sat sun "
    "january february march april may june july august september october november december "
    "jan feb mar apr jun jul aug sep sept oct nov dec am pm".split()
)


def what_clean(what):
    """Ruled 2026-10-08: "what" names the activity only. Fails on any digit or any
    whole-word weekday name, month name, or am/pm token (case-insensitive)."""
    if any(ch.isdigit() for ch in what):
        return False
    tokens = re.findall(r"[a-z]+", what.lower())
    return not any(t in WHAT_BANNED for t in tokens)


def matches(got, expected):
    """Ruled 2026-10-05: exact on GRADED keys, "what" non-empty, same length.
    Ruled 2026-10-08: "what" must also pass what_clean (no digits/weekday/month/am-pm)."""
    if not isinstance(got, list) or len(got) != len(expected):
        return False
    for g, e in zip(got, expected):
        if not isinstance(g, dict):
            return False
        if any(g.get(k) != e[k] for k in GRADED):
            return False
        if not isinstance(g.get("what"), str) or not g["what"].strip():
            return False
        if not what_clean(g["what"]):
            return False
    return True


def _whats(items):
    return [i.get("what") if isinstance(i, dict) else i for i in items]


def offline(cases):
    lines, all_ok = [], True
    for case in cases:
        handles = set(case["allowlist"]) | {case["chat"]} | {
            m["sender"] for m in case["messages"] if m["sender"] != "me"}
        for index, expected in case["targets"].items():
            i = int(index)
            target = case["messages"][i]
            tag = f"offline ({case['id']}:{i})"
            prompt = xi.build_prompt(target["text"], target["sender"],
                                     _ts(target), case["messages"][:i])
            anchor = f"{_ts(target):%Y-%m-%d %H:%M} ({_ts(target):%A})"
            leaked = sorted(h for h in handles if h in prompt)
            ok = anchor in prompt and not leaked
            detail = f"prompt anchor {anchor}" + (f" LEAKED {leaked}" if leaked else "")
            if expected == "gate":
                gated = target["sender"] not in case["allowlist"]
                ok &= gated
                detail += f"; gate: sender {'outside' if gated else 'INSIDE'} allowlist"
            else:
                kept, glog = guard(expected, case, _ts(target))
                want = expected_after_guard(case, index, expected)
                ok &= kept == want
                detail += f"; guard {'n/a' if not expected else 'drop' if not kept else 'keep'}"
                detail += "".join(f" | {g}" for g in glog)
            all_ok &= ok
            lines.append(f"{tag} {'ok  ' if ok else 'FAIL'} {detail}")
    return lines, all_ok


def live_one(tag, case, target, context, expected, index, ungraded=False):
    """One billable call. Returns (lines, ok); ok is None when ungraded."""
    try:
        got = xi.extract_text_commitments(target["text"], target["sender"],
                                          _ts(target), context)
    except Exception as exc:                      # report, never retry
        return [f"{tag} {'UNGRADED' if ungraded else 'FAIL'} error "
                f"{type(exc).__name__}: {exc}"], (None if ungraded else False)
    kept, glog = guard(got, case, _ts(target))
    if ungraded:
        out = [f"{tag} UNGRADED what={_whats(got)}"]
        out += [f"  | {g}" for g in glog]
        out.append(f"  model:    {json.dumps(got)}")
        return out, None
    want_kept = expected_after_guard(case, index, expected)
    ok = matches(got, expected) and matches(kept, want_kept)
    out = [f"{tag} {'PASS' if ok else 'FAIL'} what={_whats(got)}"]
    out += [f"  | {g}" for g in glog]
    if not ok:
        out.append(f"  model:    {json.dumps(got)}")
        out.append(f"  expected: {json.dumps(expected)}")
        if want_kept != expected:
            out.append(f"  after guard: got {json.dumps(kept)} expected {json.dumps(want_kept)}")
    return out, ok


def live(cases):
    """Result lines, all_ok, and (passed, graded, ungraded) counts."""
    lines, results = [], []
    for case in cases:
        for index, expected in case["targets"].items():
            if expected == "gate":
                continue
            i = int(index)
            out, ok = live_one(f"live ({case['id']}:{i})", case,
                               case["messages"][i], case["messages"][:i],
                               expected, index,
                               ungraded=index in case.get("ungraded", []))
            lines += out
            results.append(ok)
        if "extraction_negative" in case:
            target = case["messages"][0]
            out, ok = live_one(f"live ({case['id']}:negative)", case, target, [],
                               case["extraction_negative"], "negative")
            lines += out
            results.append(ok)
    graded = [r for r in results if r is not None]
    counts = (sum(graded), len(graded), len(results) - len(graded))
    return lines, all(graded), counts


def main():
    is_live = "--live" in sys.argv[1:]
    provenance()
    with open(FIXTURE, encoding="utf-8") as handle:
        cases = json.load(handle)["cases"]
    lines, ok = offline(cases)
    counts = None
    if is_live:
        live_lines, live_ok, counts = live(cases)
        lines, ok = lines + live_lines, ok and live_ok
    for line in lines:
        print(line)
    print(f"\n{'ALL PASS' if ok else 'NOT ALL PASS'}")
    if counts:
        print(f"live: {counts[0]}/{counts[1]} graded, {counts[2]} ungraded")
    sys.exit(0 if ok else 1)


main()
