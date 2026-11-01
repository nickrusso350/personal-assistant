"""Tests for the extraction parse guard (ruled 2026-11-01, Issue #1).

extract.extract_commitments parses the model's reply, wraps a bare object
in a list, then requires a list whose every element is a dict; anything
else raises ValueError naming the type and a repr cut to 200 characters.

Part one drives extract_commitments with a stub Anthropic client whose
reply text is each case's raw JSON. extract.load_env_file is replaced by a
no-op so the test never reads ~/.personal_assistant.env, and the stub
replaces anthropic.Anthropic so no request can leave the machine. Both are
restored in a finally.

Part two drives digest.process_messages over a three-message batch with
digest.extract_commitments replaced by a stub that raises ValueError for
one body. The state is an in-memory dict, never state.json. Pinned: the
failing message's subject is in failed, its id is not in
processed_message_ids, and the other two messages are extracted.

Cases are self-authored and inline; expected outcomes are the ruling's.

Run: python3 test_extract_guard.py
"""

import os
import socket
import subprocess

import digest
import extract

HERE = os.path.dirname(os.path.abspath(__file__))


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


class _Block:
    def __init__(self, text):
        self.text = text


class _Reply:
    def __init__(self, text):
        self.content = [_Block(text)]


class StubClient:
    """Stands in for anthropic.Anthropic(). Answers with the canned text."""

    reply_text = None
    calls = 0

    def __init__(self, *args, **kwargs):
        self.messages = self

    def create(self, **kwargs):
        StubClient.calls += 1
        return _Reply(StubClient.reply_text)


# (case, raw reply, expected): expected is "ValueError" or the returned list.
GUARD_CASES = [
    ("null", "null", "ValueError"),
    ("string", '"a commitment"', "ValueError"),
    ("number", "42", "ValueError"),
    ("list of strings", '["a", "b"]', "ValueError"),
    ("mixed list", '[{"type": "task"}, "x"]', "ValueError"),
    ("bare dict", '{"type": "task", "what": "W-9"}',
     [{"type": "task", "what": "W-9"}]),
    ("proper list", '[{"type": "task"}, {"type": "appointment"}]',
     [{"type": "task"}, {"type": "appointment"}]),
]


def run_guard():
    failures = 0
    saved = (extract.anthropic.Anthropic, extract.load_env_file)
    extract.anthropic.Anthropic = StubClient
    extract.load_env_file = lambda: None
    try:
        for case, raw, expected in GUARD_CASES:
            StubClient.reply_text = raw
            try:
                got = extract.extract_commitments("fixture message")
                shown = got
            except ValueError as error:
                got = "ValueError"
                shown = f"ValueError: {error}"
            if got == expected:
                print(f"{case}. OK — {shown!r}")
            else:
                print(f"{case}. FAIL — got {shown!r}\n         want {expected!r}")
                failures += 1
    finally:
        extract.anthropic.Anthropic, extract.load_env_file = saved
    if StubClient.calls != len(GUARD_CASES):
        print(f"stub calls. FAIL — {StubClient.calls}, want {len(GUARD_CASES)}")
        failures += 1
    return failures


def run_process_messages():
    messages = [
        {"id": "m1", "subject": "first", "sender": "a", "body": "GOOD 1"},
        {"id": "m2", "subject": "second", "sender": "b", "body": "BAD"},
        {"id": "m3", "subject": "third", "sender": "c", "body": "GOOD 3"},
    ]

    def stub_extract(body):
        if body == "BAD":
            raise ValueError("Model did not return a list of objects: str 'x'")
        return [{"type": "task", "what": body, "date": None, "time": None,
                 "action_needed": None}]

    state = {"processed_message_ids": [], "commitments": {}}
    saved = digest.extract_commitments
    digest.extract_commitments = stub_extract
    try:
        skipped, extracted, failed = digest.process_messages(
            messages, state, "2026-11-01")
    finally:
        digest.extract_commitments = saved

    got = {
        "skipped": skipped,
        "extracted": extracted,
        "failed": failed,
        "processed": state["processed_message_ids"],
        "commitments": sorted(state["commitments"]),
    }
    want = {
        "skipped": 0,
        "extracted": 2,
        "failed": ["second"],
        "processed": ["m1", "m3"],
        "commitments": ["m1:0", "m3:0"],
    }
    if got == want:
        print(f"process_messages. OK — {got!r}")
        return 0
    print(f"process_messages. FAIL — got {got!r}\n         want {want!r}")
    return 1


def main():
    provenance()
    failures = run_guard() + run_process_messages()
    if failures:
        raise SystemExit(f"\n{failures} case(s) failed.")
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
