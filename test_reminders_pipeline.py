"""Fake-backed tests for the Reminders pipeline: reconcile and write_back,
and the text path into it (digest.process_texts, wired 2026-10-10).

Standard library only, no pytest. No osascript ever runs — the three Reminders
functions are replaced with fakes that record their calls, and any fake the
test did not explicitly authorize raises AssertionError if something calls it.

The real state.json is never opened. Both modules' STATE_FILE globals are
redirected at a temp fixture file, and the tests assert against that file's
contents on disk to prove the redirect took effect rather than assuming it.

The text cases replace digest.extract_text_commitments with a counting fake,
so no API call is made; the what gate and window guard run for real. Message
records are self-authored in fetch_imessage's shape; chat.db is never read.

Run: python3 test_reminders_pipeline.py
"""

import contextlib
import copy
import io
import os
import sys
import tempfile
from datetime import datetime

import digest
import reminders_write
import resolve
from state import load_state, save_state


def forbidden(label):
    """A fake that fails the test if anything calls it."""
    def fake(*args, **kwargs):
        raise AssertionError(f"{label} must not be called (args={args!r})")
    return fake


def install(path, read=None, create=None, delete=None):
    """Point both modules' STATE_FILE at the fixture and install fakes.
    Returns a restore callable. Unsupplied fakes are forbidden."""
    saved = (
        reminders_write.STATE_FILE,
        resolve.STATE_FILE,
        reminders_write.read_completed,
        reminders_write.create_reminder,
        resolve.delete_reminder,
    )
    reminders_write.STATE_FILE = path
    resolve.STATE_FILE = path
    reminders_write.read_completed = read or forbidden("read_completed")
    reminders_write.create_reminder = create or forbidden("create_reminder")
    resolve.delete_reminder = delete or forbidden("delete_reminder")

    def restore():
        (
            reminders_write.STATE_FILE,
            resolve.STATE_FILE,
            reminders_write.read_completed,
            reminders_write.create_reminder,
            resolve.delete_reminder,
        ) = saved

    return restore


def commitment(cid, what, status="open", reminder=None):
    record = {
        "id": cid,
        "status": status,
        "type": "task",
        "what": what,
        "date": None,
        "time": None,
        "first_seen": "2026-07-01",
        "resolved_on": None,
    }
    if reminder is not None:
        record["reminder"] = reminder
    return record


def fixture_state(*commitments):
    return {
        "schema_version": 1,
        "processed_message_ids": [],
        "commitments": {c["id"]: c for c in commitments},
    }


def test_ticked_resolves_and_deletes(path):
    """A ticked reminder resolves its commitment, and the delete happens via
    resolve_one's hook — reconcile itself never deletes."""
    state = fixture_state(
        commitment("m1:0", "Pay the water bill", reminder={"reminder_id": "rid-1"})
    )
    save_state(state, path)
    reads, deletes = [], []

    def read(reminder_id):
        reads.append(reminder_id)
        return True

    def delete(reminder_id):
        deletes.append(reminder_id)

    restore = install(path, read=read, delete=delete)
    try:
        reminders_write.reconcile(state)
    finally:
        restore()

    assert reads == ["rid-1"], reads
    assert deletes == ["rid-1"], deletes
    assert state["commitments"]["m1:0"]["status"] == "resolved"

    # Content change on the fixture file proves the STATE_FILE redirect held.
    on_disk = load_state(path)
    assert on_disk["commitments"]["m1:0"]["status"] == "resolved", on_disk
    assert on_disk["commitments"]["m1:0"]["resolved_on"] is not None
    # The sub-dict is deliberately left in place — inert on a resolved item.
    assert on_disk["commitments"]["m1:0"]["reminder"]["reminder_id"] == "rid-1"
    print("  OK — ticked: resolved, deleted once, persisted to the fixture")


def test_unticked_is_untouched(path):
    """An unticked reminder changes nothing: no resolve, no delete, no write."""
    state = fixture_state(
        commitment("m2:0", "Book a dentist cleaning", reminder={"reminder_id": "rid-2"})
    )
    save_state(state, path)
    before = open(path).read()

    def read(reminder_id):
        return False

    # create and delete are forbidden — reaching either is a failure.
    restore = install(path, read=read)
    try:
        reminders_write.reconcile(state)
    finally:
        restore()

    assert state["commitments"]["m2:0"]["status"] == "open"
    assert open(path).read() == before, "fixture file must be untouched"
    print("  OK — unticked: left open, nothing written, nothing deleted")


def test_existing_sub_dict_skips_create(path):
    """write_back never re-creates a reminder for an item that has one."""
    already = commitment("m3:0", "Send the lease back", reminder={"reminder_id": "rid-3"})
    state = fixture_state(already)
    save_state(state, path)
    before = open(path).read()

    # create is forbidden — write_back must not call it for this item.
    restore = install(path)
    try:
        reminders_write.write_back(state, [already], [])
    finally:
        restore()

    assert state["commitments"]["m3:0"]["reminder"]["reminder_id"] == "rid-3"
    assert open(path).read() == before, "no save should occur with nothing to create"
    print("  OK — existing sub-dict: no create attempted, no save")


def test_one_create_failure_does_not_stop_the_rest(path):
    """A create that raises is logged and skipped; later items still process."""
    first = commitment("m4:0", "First item")
    second = commitment("m4:1", "Second item")
    third = commitment("m4:2", "Third item")
    state = fixture_state(first, second, third)
    save_state(state, path)
    attempted = []

    def create(title):
        attempted.append(title)
        if title == "Second item":
            raise RuntimeError("osascript create_reminder failed (exit 1): boom")
        return f"x-apple-reminder://FAKE-{len(attempted)}"

    restore = install(path, create=create)
    captured = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = captured
    try:
        reminders_write.write_back(state, [first, second], [third])
    finally:
        sys.stderr = real_stderr
        restore()

    assert attempted == ["First item", "Second item", "Third item"], attempted
    assert "reminder" in state["commitments"]["m4:0"]
    assert "reminder" not in state["commitments"]["m4:1"], "failed create must record nothing"
    assert "reminder" in state["commitments"]["m4:2"], "later items must still process"
    assert "m4:1" in captured.getvalue(), captured.getvalue()

    on_disk = load_state(path)
    assert "reminder" in on_disk["commitments"]["m4:2"], on_disk
    print("  OK — one create raised: logged, skipped, remaining items processed")


def text_record(guid, text="Thursday 7 works, I'll bring the wine"):
    """A fetch_imessage record. The sender is a fictional handle, as fetched."""
    return {"guid": guid, "chat": "iMessage;-;+15550100001",
            "sender": "+15550100001", "is_from_me": False,
            "ts": "2026-10-08T18:00:00-04:00", "text": text,
            "context": [{"sender": "me", "ts": "2026-10-08T17:55:00-04:00",
                         "text": "dinner Thursday?", "is_from_me": True}]}


def install_extract(fake):
    saved = digest.extract_text_commitments
    digest.extract_text_commitments = fake

    def restore():
        digest.extract_text_commitments = saved

    return restore


def test_text_keys_coexist_and_seen_set_skips(path):
    """process_texts keys imsg:<guid>:<position> by raw extraction index beside
    untouched gmail keys, adds imsg:<guid> only after success, makes zero
    extraction calls once the guid is seen, and the [text] title reaches the
    reminder."""
    gmail = commitment("m5:0", "Send the lease back")
    state = fixture_state(gmail)
    state["processed_message_ids"].append("m5")
    gmail_before = copy.deepcopy(state["commitments"]["m5:0"])
    calls = []

    def extract(message, sender, anchor_ts, context):
        calls.append((message, sender, anchor_ts, context))
        return [
            {"type": "appointment", "what": "Dinner on Thursday", "date": "2026-10-15",
             "time": "19:00", "action_needed": None},
            {"type": "task", "what": "Plan something", "date": None,
             "time": None, "action_needed": None},          # guard drops: null date
            {"type": "task", "what": "Bring the wine", "date": "2026-10-15",
             "time": None, "action_needed": None},
        ]

    restore = install_extract(extract)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            first = digest.process_texts([text_record("G1")], state, "2026-10-08")
            second = digest.process_texts([text_record("G1")], state, "2026-10-09")
    finally:
        restore()

    assert first == (0, 1, []), first
    assert second == (1, 0, []), second
    assert len(calls) == 1, "a seen guid must make zero extraction calls"
    message, sender, anchor, context = calls[0]
    assert sender == "+15550100001" and anchor == datetime.fromisoformat("2026-10-08T18:00:00-04:00")
    assert context == text_record("G1")["context"]

    keys = set(state["commitments"])
    assert keys == {"m5:0", "imsg:G1:0", "imsg:G1:2"}, keys
    assert state["commitments"]["m5:0"] == gmail_before, "gmail record must be untouched"
    assert state["processed_message_ids"] == ["m5", "imsg:G1"], state["processed_message_ids"]
    dinner = state["commitments"]["imsg:G1:0"]
    assert dinner["what"] == "[text] Dinner", dinner          # gated, then tagged
    assert (dinner["source"], dinner["sender"], dinner["subject"]) == ("imessage", "them", None)
    assert dinner["first_seen"] == "2026-10-08" and dinner["status"] == "open"
    lines = out.getvalue().splitlines()
    assert "WHAT SANITIZED before='Dinner on Thursday' after='Dinner'" in lines, lines
    assert any(l.startswith("IMESSAGE GUARD dropped date=null") for l in lines), lines

    wine = state["commitments"]["imsg:G1:2"]
    save_state(state, path)
    titles = []

    def create(title):
        titles.append(title)
        return "x-apple-reminder://FAKE-TEXT"

    restore = install(path, create=create)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            reminders_write.write_back(state, [], [wine])
    finally:
        restore()
    assert titles == ["[text] Bring the wine"], titles
    assert load_state(path)["commitments"]["imsg:G1:2"]["reminder"]["reminder_id"] \
        == "x-apple-reminder://FAKE-TEXT"
    print("  OK — imsg keys beside gmail keys; seen guid: zero calls; [text] title reaches the reminder")


def test_text_extraction_failure_retries(path):
    """A failed extraction leaves imsg:<guid> out of the seen-set, records
    nothing, and the next run retries it."""
    state = fixture_state()
    attempts = []

    def flaky(message, sender, anchor_ts, context):
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("Model did not return valid JSON: 'x'")
        return []

    restore = install_extract(flaky)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            first = digest.process_texts([text_record("G2")], state, "2026-10-08")
            second = digest.process_texts([text_record("G2")], state, "2026-10-09")
    finally:
        restore()
    assert first == (0, 0, ["imsg:G2"]), first
    assert second == (0, 1, []), second
    assert state["processed_message_ids"] == ["imsg:G2"], state["processed_message_ids"]
    assert state["commitments"] == {}
    print("  OK — failed extraction: not marked seen, retried next run")


def test_notes_render_first(path):
    """build_digest's notes are the first body line(s), on an empty morning too;
    no notes leaves the body unchanged."""
    empty = fixture_state()
    _, plain = digest.build_digest(empty, [], "2026-10-10")
    _, noted = digest.build_digest(empty, [], "2026-10-10", notes=(digest.TEXT_UNAVAILABLE,))
    assert plain == "Nothing needs your attention today.", plain
    assert noted == "Text source unavailable this morning.\n\nNothing needs your attention today.", noted
    task = commitment("m6:0", "Send the lease back")
    _, body = digest.build_digest(fixture_state(task), [], "2026-10-10",
                                  notes=(digest.TEXT_UNAVAILABLE,))
    assert body.splitlines()[:3] == ["Text source unavailable this morning.", "", "TO DO"], body
    print("  OK — notes render first, empty morning included; none leaves the body unchanged")


def main():
    real_state = os.path.abspath("state.json")
    real_before = open(real_state).read() if os.path.exists(real_state) else None

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "fixture_state.json")
        for name, test in [
            ("1. ticked -> resolved + delete", test_ticked_resolves_and_deletes),
            ("2. unticked -> untouched", test_unticked_is_untouched),
            ("3. existing sub-dict -> no create", test_existing_sub_dict_skips_create),
            ("4. one create raises -> rest continue", test_one_create_failure_does_not_stop_the_rest),
            ("5. text keys beside gmail; seen guid -> no extraction", test_text_keys_coexist_and_seen_set_skips),
            ("6. text extraction fails -> retried", test_text_extraction_failure_retries),
            ("7. notes render first", test_notes_render_first),
        ]:
            print(name)
            test(path)

    real_after = open(real_state).read() if os.path.exists(real_state) else None
    assert real_after == real_before, "the real state.json must never be written"
    print("\nAll checks passed. Real state.json untouched.")


if __name__ == "__main__":
    main()
