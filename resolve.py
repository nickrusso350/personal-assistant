
import sys
from datetime import date

from reminders_write import delete_reminder
from state import load_state, save_state, STATE_FILE


def select_by_index(items, raw):
    """Pure: (chosen_item, None) for a valid 1-based index into items, else
    (None, error_message). No input(), no printing, no state access."""
    s = raw.strip()
    if not s.isdigit() or not (1 <= int(s) <= len(items)):
        return None, f"Not a valid selection: '{raw}'."
    return items[int(s) - 1], None


def pick_open(state, read=input):
    """Interactive no-arg picker: number the open commitments (sorted by date,
    undated last), prompt for one, and resolve it against this same in-memory
    state. Invalid input re-prompts; blank input or 'q' quits with no change.
    `read` is injected for tests. Returns an exit code."""
    open_items = [c for c in state["commitments"].values() if c["status"] == "open"]
    open_items.sort(key=lambda c: c["date"] if c.get("date") else "9999-99-99")
    if not open_items:
        print("No open commitments.")
        return 0
    for i, c in enumerate(open_items, 1):
        when = c.get("date") or "no date"
        print(f"{i}) ({c['type']}) {c['what']} — {when}    {c['id']}")
    while True:
        try:
            raw = read("Resolve which? (number, q to quit): ")
        except EOFError:
            print()
            return 0
        if raw.strip().lower() in ("", "q", "quit"):
            return 0
        chosen, err = select_by_index(open_items, raw)
        if err:
            print(err)
            continue
        return resolve_one(state, chosen["id"])


def resolve_one(state, commitment_id):
    """Mark one commitment resolved. Returns exit code; only saves on success.

    The single delete site in the codebase: every resolution path — the manual
    picker, a command-line id, and reconcile folding in a ticked reminder —
    lands here, so a resolved commitment's reminder is always removed by the
    same line. State is saved before the delete is attempted, and a delete
    failure does not change the exit code: the resolution is recorded either
    way, and a lingering reminder is visible and hand-deletable. The reminder
    sub-dict is left in place — it is inert once the commitment is resolved,
    since reconcile and write_back both scan open commitments only."""
    commitments = state["commitments"]

    commitment = commitments.get(commitment_id)
    if commitment is None:
        print(f"No commitment with id '{commitment_id}'.")
        return 1

    if commitment["status"] == "resolved":
        print(f"'{commitment_id}' is already resolved (on {commitment['resolved_on']}).")
        return 1

    commitment["status"] = "resolved"
    commitment["resolved_on"] = date.today().isoformat()
    save_state(state, STATE_FILE)

    reminder = commitment.get("reminder")
    if reminder and reminder.get("reminder_id"):
        try:
            delete_reminder(reminder["reminder_id"])
        except Exception as error:
            print(
                f"resolve_one: reminder delete failed for {commitment_id}: {error}",
                file=sys.stderr,
            )

    print(f"Resolved '{commitment_id}': {commitment['what']}")
    return 0


def main(argv):
    if len(argv) == 0:
        state = load_state(STATE_FILE)
        return pick_open(state)

    if len(argv) == 1:
        state = load_state(STATE_FILE)
        return resolve_one(state, argv[0])

    print("Usage: python resolve.py [commitment_id]")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
