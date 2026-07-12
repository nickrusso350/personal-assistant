

import json
import os

STATE_FILE = "state.json"
SCHEMA_VERSION = 1


def fresh_state():
    """Return a brand-new empty state structure."""
    return {
        "schema_version": SCHEMA_VERSION,
        "processed_message_ids": [],
        "commitments": {},
    }


def load_state(path=STATE_FILE):
    """Load state from disk. Missing file = first run, return fresh state.
    A file that exists but won't parse is a loud failure, never a reset."""
    if not os.path.exists(path):
        return fresh_state()
    with open(path) as f:
        raw = f.read()
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit(
            f"{path} exists but is not valid JSON. "
            "Refusing to overwrite your commitment history — inspect the file."
        )
    return state


def save_state(state, path=STATE_FILE):
    """Write state atomically: temp file first, then rename over the real one."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, path)


if __name__ == "__main__":
    # Self-test against a separate file — never touches real state.json.
    test_path = "state_test.json"

    print("1. Load with no file present (first-run path)...")
    state = load_state(test_path)
    assert state == fresh_state(), "fresh state mismatch"
    print("   OK — fresh empty state returned")

    print("2. Add a fake commitment and save...")
    state["processed_message_ids"].append("fake_msg_001")
    state["commitments"]["fake_msg_001:0"] = {"id": "fake_msg_001:0", "what": "test item"}
    save_state(state, test_path)
    print("   OK — saved")

    print("3. Reload and verify the round trip...")
    reloaded = load_state(test_path)
    assert reloaded == state, "round trip mismatch"
    print("   OK — reloaded state matches saved state")

    os.remove(test_path)
    print("4. Test file cleaned up. All checks passed.")

