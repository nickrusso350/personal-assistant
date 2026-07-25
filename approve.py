import sys
from datetime import date

from state import load_state, save_state, STATE_FILE
from digest import derive_proposals
from calendar_write import create_event


def select_by_index(items, raw):
    """Pure: (chosen_item, None) for a valid 1-based index into items, else
    (None, error_message). No input(), no printing, no state access."""
    s = raw.strip()
    if not s.isdigit() or not (1 <= int(s) <= len(items)):
        return None, f"Not a valid selection: '{raw}'."
    return items[int(s) - 1], None


def pick_proposals(state, read=input):
    """Interactive no-arg picker: number the current proposals (sorted by date,
    matching the digest's PROPOSED order), prompt for one, then approve or reject
    it against this same in-memory state via a second prompt. The first prompt's
    invalid input re-prompts; blank/'q' quits. The second prompt fails closed —
    only 'a'/'r' decide, blank/'q'/EOF quit with no decision. `read` is injected
    for tests. Returns an exit code."""
    proposals = derive_proposals(state["commitments"], date.today().isoformat())
    # Local sort only — derive_proposals is shared with digest.py and decide_one
    # and must keep its insertion-order contract. Every proposal has a parsing
    # date, so no "9999-99-99" sentinel is needed.
    proposals = sorted(proposals, key=lambda c: c["date"])
    if not proposals:
        print("No proposals.")
        return 0
    for i, c in enumerate(proposals, 1):
        when = c.get("date") or "no date"
        print(f"{i}) ({c['type']}) {c['what']} — {when}    {c['id']}")
    while True:
        try:
            raw = read("Decide which? (number, q to quit): ")
        except EOFError:
            print()
            return 0
        if raw.strip().lower() in ("", "q", "quit"):
            return 0
        chosen, err = select_by_index(proposals, raw)
        if err:
            print(err)
            continue
        return decide_chosen(state, chosen, read)


def decide_chosen(state, chosen, read=input):
    """Second prompt for one already-selected proposal. 'a' approves, 'r'
    rejects; blank/'q'/EOF quit with no decision (fail closed); anything else
    re-prompts. Returns an exit code."""
    while True:
        try:
            answer = read("[a]pprove / [r]eject / [q]uit: ").strip().lower()
        except EOFError:
            print()
            return 0
        if answer in ("", "q", "quit"):
            return 0
        if answer == "a":
            return decide_one(state, chosen["id"])
        if answer == "r":
            return decide_one(state, chosen["id"], reject=True)
        print(f"Not a valid choice: '{answer}'.")


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
        return pick_proposals(state)

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
