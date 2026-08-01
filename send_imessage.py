import subprocess

RECIPIENT = ""  # scrubbed before push; mothballed file


def send_imessage(text, recipient=RECIPIENT):
    """Send an iMessage via AppleScript. The recipient is interpolated into the
    AppleScript source; the message text is passed only as an argv item so it is
    never interpolated into the script. Never uses a shell.

    Raises RuntimeError (including stderr) on a non-zero osascript exit.
    Returns None on success."""
    send_line = (
        f'tell application "Messages" to send (item 1 of argv) '
        f'to participant "{recipient}" of (1st account whose service type = iMessage)'
    )
    result = subprocess.run(
        [
            "osascript",
            "-e", "on run argv",
            "-e", send_line,
            "-e", "end run",
            text,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"osascript failed (exit {result.returncode}): {result.stderr}")
    return None
