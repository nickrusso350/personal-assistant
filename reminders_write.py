"""Every side effect against Apple Reminders lives here.

All three public functions shell out to osascript with the AppleScript supplied
as -e lines and every dynamic value — titles, ids, the list name — bound to
argv after a "--" separator. Nothing caller-supplied is ever interpolated into
the script source, so a title containing quotes, backslashes, or newlines is
data rather than code. Never uses a shell.
"""

import subprocess

LIST_NAME = "Daily Digest"


def _osascript(lines, *arguments, timeout=15):
    """Run an AppleScript 'on run argv' handler with arguments bound to argv.

    Each line is passed as its own -e; osascript joins them with newlines. The
    "--" separator means a value beginning with "-" is treated as an argument
    rather than an option. Returns the CompletedProcess — callers decide what a
    non-zero exit means, since delete treats one case as success."""
    command = ["osascript"]
    for line in lines:
        command += ["-e", line]
    command.append("--")
    command += [str(argument) for argument in arguments]
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def _fail(action, result):
    """Raise RuntimeError describing a failed osascript run, including stderr."""
    raise RuntimeError(
        f"osascript {action} failed (exit {result.returncode}): {result.stderr.strip()}"
    )


def _is_missing_object_error(stderr):
    """True only for the observed missing-reminder error, whose text is:

        Reminders got an error: Can’t get reminder id "..." of list "...". (-1728)

    osascript writes that apostrophe as U+2019, so both forms are matched.

    The match is anchored on "get reminder id" rather than on -1728 or a bare
    "can't get" deliberately. Structural failures carry the same -1728 code and
    the same "Can't get" prefix — "Can't get item 1 of every reminder..." is a
    bug in the script, not an absent reminder — and a looser match swallowed
    exactly that as an idempotent success."""
    text = stderr.lower()
    return "can't get reminder id" in text or "can’t get reminder id" in text


def create_reminder(title):
    """Create a reminder titled `title` in LIST_NAME and return its id as a
    string (the x-apple-reminder:// URL form). Creates LIST_NAME first if no
    list by that name exists. Raises RuntimeError including stderr on any
    osascript failure, or if osascript returns no id.

    Two sequential one-line osascript calls rather than one multi-line
    script, matching the idiom read_completed and delete_reminder use. That
    form is what makes reads and deletes run in about a second where the
    block form takes roughly fifty — but it does not rescue creation, which
    measured 45.2s on 2026-07-29 in this exact form. Creation appears
    inherently slow (mechanism unconfirmed; suspected synchronous iCloud
    commit), which is why both calls here pass timeout=90 while the other
    functions take the 15s default. Either call failing raises."""
    ensure_list = [
        "on run argv",
        'tell application "Reminders" to if not (exists list (item 1 of argv)) '
        "then make new list with properties {name:(item 1 of argv)}",
        "end run",
    ]
    result = _osascript(ensure_list, LIST_NAME, timeout=90)
    if result.returncode != 0:
        _fail("create_reminder (ensure list)", result)

    script = [
        "on run argv",
        'tell application "Reminders" to return id of (make new reminder at end '
        "of list (item 1 of argv) with properties {name:(item 2 of argv)})",
        "end run",
    ]
    result = _osascript(script, LIST_NAME, title, timeout=90)
    if result.returncode != 0:
        _fail("create_reminder", result)
    reminder_id = result.stdout.strip()
    if not reminder_id:
        raise RuntimeError("osascript create_reminder returned no reminder id")
    return reminder_id


def read_completed(reminder_id):
    """Return the completed property of the reminder with exactly this id in
    LIST_NAME, as a bool.

    A missing id is an error, never a False: AppleScript's own object
    resolution fails, osascript exits non-zero, and this raises RuntimeError.
    Only the literal stdout "true"/"false" is accepted — anything else raises
    rather than being coerced, so a silent protocol change can't be misread as
    "not done yet"."""
    script = [
        "on run argv",
        'tell application "Reminders" to return completed of '
        "(reminder id (item 2 of argv) of list (item 1 of argv))",
        "end run",
    ]
    result = _osascript(script, LIST_NAME, reminder_id)
    if result.returncode != 0:
        _fail("read_completed", result)
    value = result.stdout.strip()
    if value == "true":
        return True
    if value == "false":
        return False
    raise RuntimeError(
        f"osascript read_completed returned an unreadable value: {value!r}"
    )


def delete_reminder(reminder_id):
    """Delete the reminder with exactly this id from LIST_NAME. Returns None.

    Idempotent: if the reminder is already gone AppleScript raises -1728 with
    the specific text _is_missing_object_error matches, and deleting a deleted
    reminder is treated as success — the desired end state either way. Every
    other failure raises RuntimeError including stderr, and that now includes a
    missing LIST_NAME, which reports an unresolvable list rather than an
    unresolvable reminder."""
    script = [
        "on run argv",
        'tell application "Reminders" to delete '
        "(reminder id (item 2 of argv) of list (item 1 of argv))",
        "end run",
    ]
    result = _osascript(script, LIST_NAME, reminder_id)
    if result.returncode != 0:
        if _is_missing_object_error(result.stderr):
            return None
        _fail("delete_reminder", result)
    return None
