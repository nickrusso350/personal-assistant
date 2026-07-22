import sys
from datetime import date

from state import load_state, save_state, STATE_FILE
from digest import derive_proposals
from calendar_write import create_event


def list_proposals(state):
    """Print every current proposal, one per line, id-first."""
    proposals = derive_proposals(state["commitments"], date.today().isoformat())
    if not proposals:
        print("No proposals.")
        return
    for c in proposals:
        when = c.get("date") or "no date"
        print(f"{c['id']}  ({c['type']}) {c['what']} — {when}")


def decide_one(state, commitment_id, reject=False):
    """Approve or reject one proposal by id. Returns exit code; only saves on a
    successful decision. Membership is re-derived so stale ids never slip through.
    Approval creates the calendar event before the state is mutated or saved."""
    today = date.today().isoformat()
    proposal_ids = {c["id"] for c in derive_proposals(state["commitments"], today)}
    if commitment_id not in proposal_ids:
        print(f"'{commitment_id}' is not a current proposal.")
        return 1

    commitment = state["commitments"][commitment_id]
    if reject:
        commitment["calendar"] = {"status": "rejected", "decided_on": today}
        save_state(state, STATE_FILE)
        print(f"Rejected '{commitment_id}': {commitment['what']}")
        return 0

    event_id = create_event(commitment["what"], commitment["date"], commitment.get("time"))
    commitment["calendar"] = {
        "status": "approved",
        "decided_on": today,
        "event_id": event_id,
    }
    save_state(state, STATE_FILE)
    print(f"Approved '{commitment_id}': {commitment['what']} (event {event_id})")
    return 0


def main(argv):
    if len(argv) == 0:
        state = load_state(STATE_FILE)
        list_proposals(state)
        return 0

    if len(argv) == 1:
        state = load_state(STATE_FILE)
        return decide_one(state, argv[0])

    if len(argv) == 2 and argv[1] == "reject":
        state = load_state(STATE_FILE)
        return decide_one(state, argv[0], reject=True)

    print("Usage: python approve.py [commitment_id [reject]]")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
