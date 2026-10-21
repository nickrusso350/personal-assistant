"""Tests for the promotions boundary at the fetch stage (ruled 2026-10-20).

Messages whose Gmail labelIds carry CATEGORY_PROMOTIONS are skipped inside
fetch_gmail.fetch_recent_messages, after the full get and before the record
is shaped, and counted. The function returns (messages, skipped); the caller
(digest.fetch_inputs) prints the GMAIL FETCH line. is_promotion is a pure
predicate on message.get("labelIds", []) - the key may be absent.

fixtures/fetch_skip_fixtures.json is self-authored. Unit cases u1-u5 call
is_promotion on one message: the category beside INBOX, another category,
the key absent, an empty list, and a lowercase spelling (byte-exact). The
mixed case runs a list through fetch_recent_messages with a stub service
that answers list and get from the fixture, and pins the skipped count and
the surviving ids in order. Expected values were PINNED FROM A CAPTURED RUN,
never typed before it; a case with no "expected" key reports UNPINNED and
fails.

No I/O beyond reading the fixture and the provenance header. No state file,
no network, no credentials: the stub replaces the Gmail service object, and
importing fetch_gmail only imports the Google client libraries.

Run: python3 test_fetch_skip.py
"""

import json
import os
import socket
import subprocess

from fetch_gmail import fetch_recent_messages, is_promotion

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "fetch_skip_fixtures.json")


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


class _Call:
    """One request object: .execute() returns the canned reply."""

    def __init__(self, reply):
        self.reply = reply

    def execute(self):
        return self.reply


class _Messages:
    """users().messages(): list returns every id on one page; get by id."""

    def __init__(self, messages):
        self.by_id = {m["id"]: m for m in messages}
        self.order = [m["id"] for m in messages]

    def list(self, userId, q, maxResults, pageToken=None):
        return _Call({"messages": [{"id": i} for i in self.order]})

    def get(self, userId, id, format):
        return _Call(self.by_id[id])


class StubService:
    """Stands in for build("gmail", "v1", ...). No network."""

    def __init__(self, messages):
        self._messages = _Messages(messages)

    def users(self):
        return self

    def messages(self):
        return self._messages


def check(label, why, got, case):
    print(f"{label}. {why}")
    if "expected" not in case:
        print(f"  UNPINNED — {got!r}")
        return 1
    if got != case["expected"]:
        print(f"  FAIL — got {got!r}\n         want {case['expected']!r}")
        return 1
    print(f"  OK — {got!r}")
    return 0


def run(fixture):
    failures = 0
    for case in fixture["unit"]:
        failures += check(case["case"], case["why"],
                          is_promotion(case["message"]), case)

    case = fixture["mixed"]
    messages, skipped = fetch_recent_messages(StubService(case["messages"]),
                                              "fixture query")
    got = {"skipped": skipped, "ids": [m["id"] for m in messages]}
    failures += check(case["case"], case["why"], got, case)
    return failures


def main():
    provenance()
    with open(FIXTURE, encoding="utf-8") as handle:
        fixture = json.load(handle)
    failures = run(fixture)
    if failures:
        raise SystemExit(f"\n{failures} case(s) failed or unpinned.")
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
