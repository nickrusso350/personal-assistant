import os
import time

import requests

from env_loader import load_env_file

# Same values as watchdog.py's send. Defined here, never imported: the run and
# its monitor share no import-time point of failure.
ATTEMPTS = 3
RETRY_WAIT_SECONDS = 60


def send_push(body, title):
    """Send a Pushover notification. Reads PUSHOVER_USER_KEY and
    PUSHOVER_APP_TOKEN from the environment, raising RuntimeError naming the
    missing variable before any network activity. Returns None on success.

    Bounded retry (ruled 2026-11-01, Issue #1): up to ATTEMPTS tries,
    RETRY_WAIT_SECONDS apart, on requests.ConnectionError (ConnectTimeout
    included) and on an HTTP status of 500 or above. Each such failure prints
    one "SEND ATTEMPT i/N failed: <error>" line; after the last, RuntimeError.

    Raised at once, never retried, as before: requests.ReadTimeout, a 4xx, and
    a 200 whose Pushover status is not 1. ReadTimeout is excluded because the
    request was sent and may have been delivered; a retry risks a duplicate
    part on the phone. A connection that never opened delivered nothing, and a
    5xx is Pushover saying it did not accept the message, so those are safe to
    send again."""
    load_env_file()
    user = os.environ.get("PUSHOVER_USER_KEY")
    if not user:
        raise RuntimeError("Missing environment variable: PUSHOVER_USER_KEY")
    token = os.environ.get("PUSHOVER_APP_TOKEN")
    if not token:
        raise RuntimeError("Missing environment variable: PUSHOVER_APP_TOKEN")

    last_error = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = requests.post(
                "https://api.pushover.net/1/messages.json",
                data={"token": token, "user": user, "title": title, "message": body},
                timeout=15,
            )
            response.raise_for_status()
        except requests.ReadTimeout as e:
            raise RuntimeError(f"Pushover request failed: {e}")
        except requests.ConnectionError as e:
            last_error = e
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code < 500:
                raise RuntimeError(f"Pushover request failed: {e}")
            last_error = e
        except Exception as e:
            raise RuntimeError(f"Pushover request failed: {e}")
        else:
            if response.json().get("status") != 1:
                raise RuntimeError(f"Pushover rejected: {response.text}")
            return None
        print(f"SEND ATTEMPT {attempt}/{ATTEMPTS} failed: {last_error}")
        if attempt < ATTEMPTS:
            time.sleep(RETRY_WAIT_SECONDS)
    raise RuntimeError(f"Pushover request failed after {ATTEMPTS} attempts: {last_error}")
