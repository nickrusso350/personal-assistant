"""Tests for the API timeouts and the per-call line (ruled 2026-11-12 and
2026-11-21).

The three Anthropic clients (extract.py, extract_imessage.py, synthesize.py)
were bare constructors on SDK defaults: a 600 s read timeout and two silent
retries. Each now passes an explicit timeout at the constructor, and
digest.py prints one EXTRACT CALL line per extractor call. The proof is
offline and self-authored, never a live hang.

Part one - constructor arguments. anthropic.Anthropic is replaced by a
recording stub and each of the three functions is called once. Pinned: no
positional arguments; extraction passes exactly timeout=httpx.Timeout(30.0,
connect=5.0) and nothing else, so the SDK's retries stay at their default;
synthesis passes exactly timeout=httpx.Timeout(90.0, connect=5.0) and
max_retries=0. A plain number would have raised the connect timeout from the
SDK's 5 s to the full value, which is why the constants are httpx.Timeout
objects.

Part two - a timed-out call. The stub's create raises
anthropic.APITimeoutError for one item in a batch, driven through
digest.process_messages and digest.process_texts with the real extractors
underneath. Pinned: the item is not added to processed_message_ids, it is
recorded as failed, the loop continues to the next item, an item already
processed prints no line, and the lines read "ok" / "failed
APITimeoutError" with the position counted over the whole fetch list. The
elapsed field is checked by pattern and printed masked, since it is a clock
reading. State is an in-memory dict, never state.json.

Part three - one real timeout. A socket on 127.0.0.1, port chosen by the
OS, accepts connections and never answers. A real anthropic client is
pointed at it with a dummy key, timeout 1 second, max_retries=0. Pinned:
anthropic.APITimeoutError is raised and under 5 seconds elapse. Nothing
leaves the machine: the only address is the loopback one.

In parts one and two load_env_file is replaced by a no-op in all three
modules so the test never reads ~/.personal_assistant.env; part three
passes its key and base_url explicitly. Everything replaced is restored in
a finally. Fixtures are self-authored and inline.

Run: python3 test_api_timeouts.py
"""

import contextlib
import io
import os
import re
import socket
import subprocess
import threading
import time
from datetime import datetime

import anthropic
import httpx

import digest
import extract
import extract_imessage
import synthesize

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_ANTHROPIC = anthropic.Anthropic


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
    stop_reason = "end_turn"

    def __init__(self, text):
        self.content = [_Block(text)]


class RecordingClient:
    """Stands in for anthropic.Anthropic. Records every constructor call and
    answers each create from `script`: an entry is the reply text, or
    TIMEOUT to raise anthropic.APITimeoutError. An empty script answers []."""

    constructed = []
    script = []
    TIMEOUT = object()

    def __init__(self, *args, **kwargs):
        RecordingClient.constructed.append((args, kwargs))
        self.messages = self

    def create(self, **kwargs):
        step = RecordingClient.script.pop(0) if RecordingClient.script else "[]"
        if step is RecordingClient.TIMEOUT:
            raise anthropic.APITimeoutError(
                request=httpx.Request("POST", "http://127.0.0.1/v1/messages"))
        return _Reply(step)

    @classmethod
    def reset(cls, script=()):
        cls.constructed = []
        cls.script = list(script)


@contextlib.contextmanager
def stubbed():
    """Swap in RecordingClient and silence load_env_file in all three modules."""
    saved = (anthropic.Anthropic, extract.load_env_file,
             extract_imessage.load_env_file, synthesize.load_env_file)
    anthropic.Anthropic = RecordingClient
    extract.load_env_file = lambda: None
    extract_imessage.load_env_file = lambda: None
    synthesize.load_env_file = lambda: None
    try:
        yield
    finally:
        (anthropic.Anthropic, extract.load_env_file,
         extract_imessage.load_env_file, synthesize.load_env_file) = saved


def check(label, got, want):
    if got == want:
        print(f"{label}. OK — {got!r}")
        return 0
    print(f"{label}. FAIL — got {got!r}\n         want {want!r}")
    return 1


# ─── Part one: constructor arguments ────────────────────────────

ANCHOR = datetime.fromisoformat("2026-11-21T09:00:00-05:00")

SITES = [
    ("extract.extract_commitments",
     lambda: extract.extract_commitments("fixture message"),
     ((), {"timeout": httpx.Timeout(30.0, connect=5.0)})),
    ("extract_imessage.extract_text_commitments",
     lambda: extract_imessage.extract_text_commitments(
         "fixture text", "me", ANCHOR, []),
     ((), {"timeout": httpx.Timeout(30.0, connect=5.0)})),
    ("synthesize._call_model",
     lambda: synthesize._call_model("fixture prompt", log=False),
     ((), {"timeout": httpx.Timeout(90.0, connect=5.0), "max_retries": 0})),
]


def run_constructor_arguments():
    failures = 0
    with stubbed():
        for label, call, want in SITES:
            RecordingClient.reset()
            call()
            failures += check(label, RecordingClient.constructed, [want])
    return failures


# ─── Part two: a timed-out call leaves its item unprocessed ─────

LINE = re.compile(r"^(EXTRACT CALL (?:mail|text) \d+/\d+) \d+\.\d(s .*)$")


def masked(text):
    """Captured stdout as lines, with each EXTRACT CALL elapsed field masked.
    A line that does not fit the ruled form is returned as it is, so it
    fails the comparison."""
    lines = []
    for line in text.splitlines():
        match = LINE.match(line)
        lines.append(f"{match.group(1)} N.N{match.group(2)}" if match else line)
    return lines


def run_mail_timeout():
    messages = [
        {"id": "m0", "subject": "seen", "sender": "a", "body": "fixture 0"},
        {"id": "m1", "subject": "first", "sender": "b", "body": "fixture 1"},
        {"id": "m2", "subject": "second", "sender": "c", "body": "fixture 2"},
        {"id": "m3", "subject": "third", "sender": "d", "body": "fixture 3"},
    ]
    state = {"processed_message_ids": ["m0"], "commitments": {}}
    out = io.StringIO()
    with stubbed():
        RecordingClient.reset(["[]", RecordingClient.TIMEOUT, "[]"])
        with contextlib.redirect_stdout(out):
            skipped, extracted, failed = digest.process_messages(
                messages, state, "2026-11-21")
    got = {
        "skipped": skipped,
        "extracted": extracted,
        "failed": failed,
        "processed": state["processed_message_ids"],
        "lines": masked(out.getvalue()),
    }
    want = {
        "skipped": 1,
        "extracted": 2,
        "failed": ["second"],
        "processed": ["m0", "m1", "m3"],
        "lines": [
            "EXTRACT CALL mail 2/4 N.Ns ok",
            "EXTRACT CALL mail 3/4 N.Ns failed APITimeoutError",
            "EXTRACT CALL mail 4/4 N.Ns ok",
        ],
    }
    return check("process_messages", got, want)


def text_record(guid):
    """A fetch_imessage record. The handle is fictional."""
    return {"guid": guid, "chat": "iMessage;-;+15550100001",
            "sender": "+15550100001", "is_from_me": False,
            "ts": "2026-11-21T09:00:00-05:00", "text": "fixture text",
            "context": []}


def run_text_timeout():
    texts = [text_record("G0"), text_record("G1"), text_record("G2"),
             text_record("G3")]
    state = {"processed_message_ids": ["imsg:G0"], "commitments": {}}
    out = io.StringIO()
    with stubbed():
        RecordingClient.reset(["[]", RecordingClient.TIMEOUT, "[]"])
        with contextlib.redirect_stdout(out):
            skipped, extracted, failed = digest.process_texts(
                texts, state, "2026-11-21")
    got = {
        "skipped": skipped,
        "extracted": extracted,
        "failed": failed,
        "processed": state["processed_message_ids"],
        "lines": masked(out.getvalue()),
    }
    want = {
        "skipped": 1,
        "extracted": 2,
        "failed": ["imsg:G2"],
        "processed": ["imsg:G0", "imsg:G1", "imsg:G3"],
        "lines": [
            "EXTRACT CALL text 2/4 N.Ns ok",
            "EXTRACT CALL text 3/4 N.Ns failed APITimeoutError",
            "EXTRACT CALL text 4/4 N.Ns ok",
        ],
    }
    return check("process_texts", got, want)


# ─── Part three: one real timeout against a silent local server ─

REAL_TIMEOUT_SECONDS = 1
REAL_CEILING_SECONDS = 5


def run_real_timeout():
    """A real client against a loopback socket that accepts and never
    answers. Not tuned: if this is flaky, that is a finding to report."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]
    held = []

    def accept_and_hold():
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            held.append(connection)

    thread = threading.Thread(target=accept_and_hold, daemon=True)
    thread.start()

    raised = None
    started = time.monotonic()
    try:
        client = REAL_ANTHROPIC(api_key="dummy-key-for-a-local-socket",
                                base_url=f"http://127.0.0.1:{port}",
                                timeout=REAL_TIMEOUT_SECONDS, max_retries=0)
        client.messages.create(
            model="claude-sonnet-4-6", max_tokens=10,
            messages=[{"role": "user", "content": "fixture"}],
        )
    except Exception as error:
        raised = type(error).__name__
    finally:
        elapsed = time.monotonic() - started
        for connection in held:
            connection.close()
        server.close()

    got = {
        "raised": raised,
        "accepted": len(held),
        "under_ceiling": elapsed < REAL_CEILING_SECONDS,
        "waited_for_timeout": elapsed >= REAL_TIMEOUT_SECONDS,
    }
    want = {
        "raised": "APITimeoutError",
        "accepted": 1,
        "under_ceiling": True,
        "waited_for_timeout": True,
    }
    return check("real timeout on 127.0.0.1", got, want)


def main():
    provenance()
    failures = (run_constructor_arguments() + run_mail_timeout()
                + run_text_timeout() + run_real_timeout())
    if failures:
        raise SystemExit(f"\n{failures} case(s) failed.")
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
