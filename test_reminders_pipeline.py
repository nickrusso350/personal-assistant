"""Fake-backed tests for the Reminders pipeline: reconcile and write_back.

Standard library only, no pytest. No osascript ever runs — the three Reminders
functions are replaced with fakes that record their calls, and any fake the
test did not explicitly authorize raises AssertionError if something calls it.

The real state.json is never opened. Both modules' STATE_FILE globals are
redirected at a temp fixture file, and the tests assert against that file's
contents on disk to prove the redirect took effect rather than assuming it.

Run: python3 test_reminders_pipeline.py
"""

import io
import os
import sys
import tempfile

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
        ]:
            print(name)
            test(path)

    real_after = open(real_state).read() if os.path.exists(real_state) else None
    assert real_after == real_before, "the real state.json must never be written"
    print("\nAll checks passed. Real state.json untouched.")


if __name__ == "__main__":
    main()
