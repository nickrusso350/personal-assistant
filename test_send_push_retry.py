"""Tests for send_push's bounded retry (ruled 2026-11-01, Issue #1).

send_push retries on requests.ConnectionError (ConnectTimeout included) and
on an HTTP status of 500 or above, up to ATTEMPTS tries RETRY_WAIT_SECONDS
apart, printing one SEND ATTEMPT line per such failure. ReadTimeout, a 4xx,
and a 200 whose Pushover status is not 1 raise at once.

Each case scripts requests.post as a sequence of outcomes: an exception
instance to raise, or (http status, Pushover status) for a real
requests.Response. time.sleep is replaced by a recorder, so nothing waits.
send_push.load_env_file is replaced by a no-op so the test never reads
~/.personal_assistant.env; the two Pushover variables are set to fake values
in os.environ for the run and restored after. Nothing leaves the machine.

Pinned per case: outcome (None or RuntimeError), post calls, sleep calls, and
SEND ATTEMPT lines. Cases are self-authored and inline; expected outcomes are
the ruling's.

Run: python3 test_send_push_retry.py
"""

import contextlib
import io
import json
import os
import socket
import subprocess

import requests

import send_push

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_KEYS = ("PUSHOVER_USER_KEY", "PUSHOVER_APP_TOKEN")


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


def response(http_status, pushover_status):
    """A real requests.Response, so raise_for_status behaves as in production."""
    r = requests.Response()
    r.status_code = http_status
    r.url = "https://api.pushover.net/1/messages.json"
    r.reason = "fixture"
    r._content = json.dumps({"status": pushover_status}).encode()
    return r


OK = (200, 1)

# (case, scripted outcomes, expected {outcome, calls, sleeps, attempt_lines})
CASES = [
    ("ConnectionError then success",
     [requests.ConnectionError("fixture refused"), OK],
     {"outcome": None, "calls": 2, "sleeps": 1, "attempt_lines": 1}),
    ("ConnectTimeout then success",
     [requests.ConnectTimeout("fixture connect timeout"), OK],
     {"outcome": None, "calls": 2, "sleeps": 1, "attempt_lines": 1}),
    ("HTTP 503 then success",
     [(503, 0), OK],
     {"outcome": None, "calls": 2, "sleeps": 1, "attempt_lines": 1}),
    ("ReadTimeout",
     [requests.ReadTimeout("fixture read timeout"), OK],
     {"outcome": "RuntimeError", "calls": 1, "sleeps": 0, "attempt_lines": 0}),
    ("HTTP 400",
     [(400, 0), OK],
     {"outcome": "RuntimeError", "calls": 1, "sleeps": 0, "attempt_lines": 0}),
    ("200 with Pushover status 0",
     [(200, 0), OK],
     {"outcome": "RuntimeError", "calls": 1, "sleeps": 0, "attempt_lines": 0}),
    ("three ConnectionErrors",
     [requests.ConnectionError("fixture refused")] * 3,
     {"outcome": "RuntimeError", "calls": 3, "sleeps": 2, "attempt_lines": 3}),
]


def run_case(script):
    calls = []
    sleeps = []
    outcomes = iter(script)

    def stub_post(url, data, timeout):
        calls.append(url)
        step = next(outcomes)
        if isinstance(step, Exception):
            raise step
        return response(*step)

    saved = (send_push.requests.post, send_push.time.sleep,
             send_push.load_env_file)
    send_push.requests.post = stub_post
    send_push.time.sleep = sleeps.append
    send_push.load_env_file = lambda: None
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            try:
                outcome = send_push.send_push("fixture body", "fixture title")
            except RuntimeError as error:
                outcome = "RuntimeError"
                out.write(f"(raised: {error})\n")
    finally:
        (send_push.requests.post, send_push.time.sleep,
         send_push.load_env_file) = saved
    lines = out.getvalue().splitlines()
    got = {
        "outcome": outcome,
        "calls": len(calls),
        "sleeps": len(sleeps),
        "attempt_lines": sum(l.startswith("SEND ATTEMPT ") for l in lines),
    }
    return got, sleeps, lines


def main():
    provenance()
    saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
    for k in ENV_KEYS:
        os.environ[k] = "fixture-" + k.lower()
    failures = 0
    try:
        for case, script, want in CASES:
            got, sleeps, lines = run_case(script)
            if sleeps and set(sleeps) != {send_push.RETRY_WAIT_SECONDS}:
                got["sleep_seconds"] = sleeps
            if got == want:
                print(f"{case}. OK — {got!r}")
            else:
                print(f"{case}. FAIL — got {got!r}\n         want {want!r}")
                failures += 1
            for line in lines:
                print(f"    {line}")
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    if failures:
        raise SystemExit(f"\n{failures} case(s) failed.")
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
