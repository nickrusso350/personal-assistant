
import sys
from datetime import date

from state import load_state, save_state, STATE_FILE


def list_open(state):
    """Print every open commitment, one per line, sorted by date (undated last)."""
    commitments = state["commitments"]
    open_items = [c for c in commitments.values() if c["status"] == "open"]
    open_items.sort(key=lambda c: c["date"] if c.get("date") else "9999-99-99")
    if not open_items:
        print("No open commitments.")
        return
    for c in open_items:
        when = c.get("date") or "no date"
        print(f"{c['id']}  ({c['type']}) {c['what']} — {when}")


def resolve_one(state, commitment_id):
    """Mark one commitment resolved. Returns exit code; only saves on success."""
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
    print(f"Resolved '{commitment_id}': {commitment['what']}")
    return 0


def main(argv):
    if len(argv) == 0:
        state = load_state(STATE_FILE)
        list_open(state)
        return 0

    if len(argv) == 1:
        state = load_state(STATE_FILE)
        return resolve_one(state, argv[0])

    print("Usage: python resolve.py [commitment_id]")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
