import os
import requests

from env_loader import load_env_file


def send_push(body, title):
    """Send a Pushover notification. Reads PUSHOVER_USER_KEY and
    PUSHOVER_APP_TOKEN from the environment, raising RuntimeError naming the
    missing variable before any network activity. Raises RuntimeError on a
    network/HTTP failure or when Pushover's response status is not 1. Returns
    None on success."""
    load_env_file()
    user = os.environ.get("PUSHOVER_USER_KEY")
    if not user:
        raise RuntimeError("Missing environment variable: PUSHOVER_USER_KEY")
    token = os.environ.get("PUSHOVER_APP_TOKEN")
    if not token:
        raise RuntimeError("Missing environment variable: PUSHOVER_APP_TOKEN")

    try:
        response = requests.post(
            "https://api.pushover.net/1/messages.json",
            data={"token": token, "user": user, "title": title, "message": body},
            timeout=15,
        )
        response.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Pushover request failed: {e}")

    if response.json().get("status") != 1:
        raise RuntimeError(f"Pushover rejected: {response.text}")
    return None
