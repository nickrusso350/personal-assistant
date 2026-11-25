#!/usr/bin/env python3
"""Tests for the model-written wave-tops script and the rental silence
(ruled 2026-11-24), on fixtures/wavetops_model_script_fixtures.json.

Offline and self-authored: invented data only, never real mail, never the
API. anthropic.Anthropic is replaced by a recording stub and
wavetops.load_env_file by a no-op, so no call leaves the machine and the
env file is never read; both are restored in a finally, and the run prints
that they are back. This script never opens the real groupings directory,
touches no state, sends nothing, and writes nothing.

Part one - rental silence (R2) and its discriminator. Each fixture case
runs through wavetops.script(), the pattern writer, which reads the same
fact sheet the model writer does. Lines and log lines are pinned exactly; a
case with no "expected" prints UNPINNED and counts as a failure.

Part two - check_script(). Each check case feeds fact-sheet rows and
candidate lines; the expected reason (or null for a pass) is exact. Then
every phrase in INSTRUCTION_PHRASES and every word in RELATIVE_DAY_WORDS is
placed in an otherwise passing script and must fail with its own reason.

Part three - what the model sees. The sheet text for the model_week case is
pinned; the prompt is the approved text with that sheet in place, and holds
no record id, ref, date, raw clock or location.

Part four - the writer. model_script() over a stubbed client: passes first
try; fails the check then passes; fails twice and falls back to the pattern
script, byte-identical to script(); a raised call then a pass; two raised
calls; a reply cut at max_tokens; a clear week and a fallback grouping,
which construct no client. Pinned per scenario: the lines, the log lines,
the number of calls, and the prompt of every call. Constructor arguments
are asserted on every construction: no positional arguments, exactly
timeout=httpx.Timeout(30.0, connect=5.0) and max_retries=0; every create
call passes exactly model=synthesize.MODEL, max_tokens=400, temperature=0
and one user message holding the prompt.

The retry carries the reason (ruled 2026-11-24): the first call's prompt is
the approved text with the sheet; after a failed check or a truncated reply
the second call's prompt is the first, a blank line, and the note for that
class of failure; after a call error it is the first again. The notes are
typed here from the ruling, not imported, so the test pins their text. No
note holds a title or a value from the fixture, and no second prompt holds
the rejected reply.

Any failure exits non-zero. An md5 over the result lines closes the run.

Usage: python3 test_wavetops_model_script.py
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

import anthropic                                                # noqa: E402
import httpx                                                    # noqa: E402

import destinations                                             # noqa: E402
import synthesize                                               # noqa: E402
import wavetops                                                 # noqa: E402

FIXTURE = os.path.join(REPO, "fixtures", "wavetops_model_script_fixtures.json")
RUN_DATE = datetime.date(2027, 3, 7)

GOOD = ["On Monday you have Fixture review at 9 AM and a flight at 5 PM to Fixtureville.",
        "On Wednesday you have Sample lunch at 2:30 PM Central."]
BAD = ["On Monday you have Fixture review at 9 AM and a flight to Fixtureville.",
       "On Wednesday you have Sample lunch at 2:30 PM Central."]
EXPECTED_SHEET = ("1. event | day: Monday | time: 9 AM | title: Fixture review\n"
                  "2. flight | day: Monday | time: 5 PM | destination: Fixtureville\n"
                  "3. event | day: Wednesday | time: 2:30 PM | zone: Central | title: Sample lunch")
EXPECTED_TRIP_SHEET = ("1. trip | first day: Monday | last day: Wednesday | destination: Fixtureville\n"
                       "2. trip | first day: Saturday | continues past the week\n"
                       "3. flight | day: Friday")
FORMAT_NOTE = ("Your previous script was rejected for its format. Reply with the script "
               "only: one sentence per line, each ending in a period, no greeting, and no "
               "more lines than the fact sheet has items.")
NOTES = {
    "a": ("Your previous script was rejected: it left out or altered a day, time, zone "
          "or destination from the fact sheet. Copy each one exactly."),
    "b": ("Your previous script was rejected: it contained a day, number or time that is "
          "not on the fact sheet. Use only what the sheet gives."),
    "c": ("Your previous script was rejected: it contained an instruction, a reminder or "
          "a relative day word. State only what exists. A word such as mandatory may "
          "describe an item; it is never turned into an instruction."),
    "d": FORMAT_NOTE,
    "empty": FORMAT_NOTE,
    "truncated": "Your previous script was too long. Write it more briefly.",
}
FIXTURE_VALUES = ("Fixture review", "Fixtureville", "Sample lunch", "Monday", "Wednesday",
                  "Central", "9 AM", "5 PM", "2:30 PM")


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


def timeout_error():
    return anthropic.APITimeoutError(
        request=httpx.Request("POST", "http://127.0.0.1/v1/messages"))


class Recorder:
    """Stands in for anthropic.Anthropic. Records every construction and
    every create call; replies from a queue. A queued exception is raised; a
    (text, stop_reason) pair or a bare text is returned as a response."""

    def __init__(self):
        self.constructions, self.creates, self.replies = [], [], []

    def __call__(self, *args, **kwargs):
        self.constructions.append((args, kwargs))
        return self

    @property
    def messages(self):
        return self

    def create(self, *args, **kwargs):
        self.creates.append((args, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        text, stop = reply if isinstance(reply, tuple) else (reply, "end_turn")

        class Response:
            content = [type("Block", (), {"text": text})()]
            stop_reason = stop
        return Response()

    def reset(self, replies):
        self.constructions, self.creates, self.replies = [], [], list(replies)


def captured(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        value = fn(*args)
    return value, out.getvalue().splitlines()


class Tally:
    def __init__(self):
        self.results, self.failures = [], 0

    def record(self, name, ok, got, status=None):
        status = status or ("PASS" if ok else "FAIL")
        self.failures += status != "PASS"
        self.results.append(f"{name}: {json.dumps(got)}")
        print(f"{status} {name}")
        return status


def part_one(doc, tally):
    print("PART ONE - rental silence through script()")
    for case in doc["cases"]:
        run_date = datetime.date.fromisoformat(case["run_date"])
        lines, log = captured(wavetops.script, case["grouping"], run_date)
        got = {"lines": lines, "log": log}
        want = case.get("expected")
        status = "UNPINNED" if want is None else None
        tally.record(case["name"], want == got, got, status)
        for line in lines:
            print(f"    | {line}")
        for line in log:
            print(f"    log: {line}")
        print(f"    clause: {case['clause']}")


def part_two(doc, tally):
    print("\nPART TWO - check_script()")
    for case in doc["check_cases"]:
        got = wavetops.check_script(case["lines"], case["rows"])
        tally.record(f"check {case['name']}", got == case["expected"], got)
        print(f"    reason: {got}    clause: {case['clause']}")
    rows = next(c for c in doc["check_cases"] if c["name"] == "good_single")["rows"]
    for words, reason in ((wavetops.INSTRUCTION_PHRASES, "check c phrase"),
                          (wavetops.RELATIVE_DAY_WORDS, "check c relative")):
        for phrase in words:
            lines = [f"You have a Friday flight at 2:24 PM to Fixtureville, {phrase} it."]
            got = wavetops.check_script(lines, rows)
            tally.record(f"banned {phrase!r}", got == reason, got)
    print(f"    phrases: {len(wavetops.INSTRUCTION_PHRASES)}  "
          f"relative day words: {len(wavetops.RELATIVE_DAY_WORDS)}")


def part_three(week, tally):
    print("\nPART THREE - what the model sees")
    rows, _ = captured(wavetops.fact_sheet, week, RUN_DATE)
    sheet = wavetops._sheet_text(rows)
    tally.record("sheet text", sheet == EXPECTED_SHEET, sheet)
    for line in sheet.splitlines():
        print(f"    | {line}")
    blank = dict.fromkeys(("time", "zone", "destination", "title", "through"))
    trip_rows = [
        {**blank, "kind": "trip", "weekday": "Monday", "destination": "Fixtureville",
         "through": "Wednesday", "open_ended": False},
        {**blank, "kind": "trip", "weekday": "Saturday", "open_ended": True},
        {**blank, "kind": "flight", "weekday": "Friday", "open_ended": False},
    ]
    trip_sheet = wavetops._sheet_text(trip_rows)
    tally.record("sheet text, trip and bare rows", trip_sheet == EXPECTED_TRIP_SHEET, trip_sheet)
    for line in trip_sheet.splitlines():
        print(f"    | {line}")
    prompt = wavetops._script_prompt(rows)
    whole = prompt == wavetops.SCRIPT_PROMPT.replace("{sheet}", EXPECTED_SHEET)
    tally.record("prompt is the approved text with the sheet in place", whole, whole)
    leaks = [needle for needle in ("fx-r", "2027-", "09:00", "17:00", "14:30",
                                   "America/", "Example Way", "QXA", "QXB", "{sheet}")
             if needle in prompt]
    leaks += [r["id"] for r in week["records"] if f'"{r["id"]}"' in prompt]
    tally.record("prompt holds no pipeline key, date, raw clock or location", not leaks, leaks)


def part_four(doc, week, tally, stub):
    print("\nPART FOUR - model_script() over a stubbed client")
    pattern, _ = captured(wavetops.script, week, RUN_DATE)
    greeting = [wavetops.GREETING]
    select = "WAVETOPS SELECT 3 of 3 groups"
    clear = {"groups": [], "cause": None, "records": []}
    fallback = {"groups": None, "cause": "network", "records": []}
    c_bad = [GOOD[0], GOOD[1][:-1] + ", so please note it."]
    b_bad = [GOOD[0], GOOD[1][:-1] + ", back Saturday."]
    d_bad = ["Here is the script:"] + GOOD
    # (name, grouping, replies, lines, log, calls, note key of the 2nd prompt)
    scenarios = [
        ("passes first try", week, [GOOD], greeting + GOOD,
         [select, "WAVETOPS SCRIPT MODEL ok attempt 1"], 1, None),
        ("blank lines and spaces around the reply are dropped", week,
         ["\n  " + GOOD[0] + "  \n\n" + GOOD[1] + "\n"], greeting + GOOD,
         [select, "WAVETOPS SCRIPT MODEL ok attempt 1"], 1, None),
        ("check a fails, retry carries the check a note and passes", week,
         [BAD, GOOD], greeting + GOOD,
         [select, "WAVETOPS SCRIPT RETRY check a clock",
          "WAVETOPS SCRIPT MODEL ok attempt 2"], 2, "a"),
        ("check b fails, retry carries the check b note and passes", week,
         [b_bad, GOOD], greeting + GOOD,
         [select, "WAVETOPS SCRIPT RETRY check b weekday",
          "WAVETOPS SCRIPT MODEL ok attempt 2"], 2, "b"),
        ("check c fails, retry carries the check c note and passes", week,
         [c_bad, GOOD], greeting + GOOD,
         [select, "WAVETOPS SCRIPT RETRY check c phrase",
          "WAVETOPS SCRIPT MODEL ok attempt 2"], 2, "c"),
        ("check d fails, retry carries the format note and passes", week,
         [d_bad, GOOD], greeting + GOOD,
         [select, "WAVETOPS SCRIPT RETRY check d period",
          "WAVETOPS SCRIPT MODEL ok attempt 2"], 2, "d"),
        ("empty reply, retry carries the format note and passes", week,
         ["", GOOD], greeting + GOOD,
         [select, "WAVETOPS SCRIPT RETRY empty",
          "WAVETOPS SCRIPT MODEL ok attempt 2"], 2, "empty"),
        ("check fails twice, pattern fallback", week, [BAD, [GOOD[0]]], pattern,
         [select, "WAVETOPS SCRIPT RETRY check a clock",
          "WAVETOPS SCRIPT FALLBACK check a weekday"], 2, "a"),
        ("call raises, second prompt is the first, retry passes", week,
         [timeout_error(), GOOD], greeting + GOOD,
         [select, "WAVETOPS SCRIPT RETRY call APITimeoutError",
          "WAVETOPS SCRIPT MODEL ok attempt 2"], 2, None),
        ("call raises twice, pattern fallback", week,
         [timeout_error(), timeout_error()], pattern,
         [select, "WAVETOPS SCRIPT RETRY call APITimeoutError",
          "WAVETOPS SCRIPT FALLBACK call APITimeoutError"], 2, None),
        ("reply cut at max_tokens, retry carries the truncated note, cut again", week,
         [("\n".join(GOOD), "max_tokens"), ("\n".join(GOOD), "max_tokens")], pattern,
         [select, "WAVETOPS SCRIPT RETRY truncated",
          "WAVETOPS SCRIPT FALLBACK truncated"], 2, "truncated"),
        ("clear week makes no call", clear, [], greeting + [wavetops.CLEAR_WEEK],
         ["WAVETOPS SELECT 0 of 0 groups: no vital groups"], 0, None),
        ("fallback grouping: no script, no call", fallback, [], [],
         ["WAVETOPS SELECT 0 of 0 groups: fallback: network"], 0, None),
    ]
    prompt = wavetops.SCRIPT_PROMPT.replace("{sheet}", EXPECTED_SHEET)
    want_ctor = ((), {"timeout": httpx.Timeout(30.0, connect=5.0), "max_retries": 0})

    def create_with(content):
        return ((), {"model": synthesize.MODEL, "max_tokens": 400, "temperature": 0,
                     "messages": [{"role": "user", "content": content}]})

    for name, grouping, replies, want_lines, want_log, want_calls, note in scenarios:
        replies = ["\n".join(r) if isinstance(r, list) else r for r in replies]
        stub.reset(replies)
        lines, log = captured(wavetops.model_script, grouping, RUN_DATE)
        second = prompt if note is None else prompt + "\n\n" + NOTES[note]
        want_creates = [create_with(p) for p in (prompt, second)][:want_calls]
        ctor_ok = (len(stub.constructions) == want_calls
                   and all(c == want_ctor for c in stub.constructions))
        create_ok = stub.creates == want_creates
        # the rejected reply never rides into the second prompt
        echo = (want_calls == 2 and isinstance(replies[0], str) and replies[0] != ""
                and replies[0] in stub.creates[1][1]["messages"][0]["content"])
        got = {"lines": lines, "log": log, "calls": len(stub.creates),
               "constructor ok": ctor_ok, "create ok": create_ok,
               "second prompt": "none" if want_calls < 2 else
               "first" if note is None else f"first + note {note}",
               "rejected reply echoed": echo}
        ok = (lines == want_lines and log == want_log and ctor_ok and create_ok
              and not echo and not stub.replies)
        tally.record(name, ok, got)
        for line in lines or ["(no script)"]:
            print(f"    | {line}")
        for line in log:
            print(f"    log: {line}")
        print(f"    calls: {len(stub.creates)}  constructor ok: {ctor_ok}  "
              f"create ok: {create_ok}  second prompt: {got['second prompt']}")
    pinned = {" ".join(("check", k)) if len(k) == 1 else k: v for k, v in NOTES.items()}
    tally.record("RETRY_NOTES is the ruled text", wavetops.RETRY_NOTES == pinned,
                 wavetops.RETRY_NOTES == pinned)
    leaks = sorted({value for value in FIXTURE_VALUES
                    for text in wavetops.RETRY_NOTES.values() if value in text})
    tally.record("no note holds a title or a value from the fixture", not leaks, leaks)
    lookups = {reason: wavetops._retry_note(reason) for reason in (
        "check a weekday", "check a clock", "check a zone", "check a destination",
        "check b weekday", "check b number", "check b clock", "check c phrase",
        "check c relative", "check d period", "check d greeting", "check d lines",
        "empty", "truncated", "call APITimeoutError")}
    want = {r: (None if r.startswith("call ") else
                NOTES[r.split()[1] if r.startswith("check ") else r]) for r in lookups}
    tally.record("every reason maps to its class's note; a call error to none",
                 lookups == want, sorted(k for k in lookups if lookups[k] != want[k]))
    same = pattern == wavetops._pattern_lines(captured(wavetops.fact_sheet, week, RUN_DATE)[0])
    tally.record("fallback lines are script()'s lines", same, same)
    print(f"    model constant: synthesize.MODEL is wavetops.MODEL: "
          f"{synthesize.MODEL is wavetops.MODEL}")


def main():
    provenance()
    doc = json.load(open(FIXTURE, encoding="utf-8"))
    week = next(c for c in doc["cases"] if c["name"] == "model_week")["grouping"]
    tally, stub = Tally(), Recorder()
    saved = (anthropic.Anthropic, wavetops.load_env_file, destinations._overrides)
    anthropic.Anthropic = stub
    wavetops.load_env_file = lambda: None
    destinations._overrides = lambda: doc["overrides"]
    try:
        part_one(doc, tally)
        part_two(doc, tally)
        part_three(week, tally)
        part_four(doc, week, tally, stub)
    finally:
        anthropic.Anthropic, wavetops.load_env_file, destinations._overrides = saved
    restored = (anthropic.Anthropic is saved[0] and wavetops.load_env_file is saved[1]
                and destinations._overrides is saved[2])
    print(f"\nresults: {len(tally.results)}  failures: {tally.failures}")
    print(f"client, env loader and overrides loader restored: {restored}")
    digest = hashlib.md5("\n".join(tally.results).encode()).hexdigest()
    print(f"result md5: {digest}")
    if tally.failures or not restored:
        raise SystemExit(f"{tally.failures} result(s) failed.")


if __name__ == "__main__":
    main()
