import anthropic
import json
from datetime import date

from env_loader import load_env_file

# ─── The extraction brain ───────────────────────────────────────
def extract_commitments(message):
    """Take one message string, return a LIST of commitment dicts.
    Each dict has keys: type, what, date, time, action_needed.
    Returns an empty list [] when the message contains no commitment.
    """
    today = date.today().isoformat()
    prompt = """You are a precise data-extraction tool. Extract all commitments from the message below and return them as a JSON array of objects.
Today's date is {today}. Use it as the anchor for any relative date reasoning.
Return ONLY the JSON array. No preamble, no explanation, no markdown code fences. Your entire response must be valid, parseable JSON and nothing else.

Most messages contain NO commitment — promotions, newsletters, notifications, receipts, and general updates. When a message contains no commitment you own, return an empty array: []

A message may contain MORE THAN ONE commitment. Return one object per commitment. If a single message asks you to do two separate things (for example, send a form by one date AND attend a call at another time), return two objects — one for each.

Each object in the array uses exactly this structure:
{{"type": "...", "what": "...", "date": "...", "time": "...", "action_needed": "..."}}

Field rules:
- "type": one of "appointment", "task", or "reply_needed".

    OWNERSHIP TEST — a commitment must be an obligation YOU personally own.
    Some messages use task-shaped or reply-shaped language but the obligation
    actually sits with the SENDER, not you. Do NOT create an object for these —
    omit them from the array entirely:
      - A company soliciting a favor — "review your purchase", "rate your
        order", "take our survey", "leave feedback" — is not a task.
        You never committed to it.
      - A courtesy acknowledgment where the other party holds the next move —
        "thanks, we'll take a look and get back to you", "keep us posted if
        anything changes", "we'll be in touch" — is not reply_needed.
        They owe the next step, not you.

    Only include a commitment when the message explicitly states it AND the
    recipient owns it:
    "appointment" = a scheduled event at a date/time.
    "task" = something YOU need to do by a date — that you committed to or are
        responsible for, not a favor a company is requesting.
    "reply_needed" = someone is specifically waiting on and requesting a
        response FROM YOU now (for example, "please reply to confirm") — not a
        standing courtesy where the sender will follow up.
    Do not infer or invent a commitment that the message does not explicitly
    state. When in doubt, omit it from the array.
- "what": a short description of the commitment.
- "date": the relevant date in YYYY-MM-DD format. If the message gives a date with no year, assume the next future occurrence of that date relative to today. If no date is present, use null.
- "time": the time of day in 24-hour HH:MM format. If no time is present, use null.
- "action_needed": a concrete action the recipient is explicitly required to take. Default to null. Only fill this in if the message directly instructs the recipient to DO something. Do not infer, invent, or imply an action that the message does not explicitly state. The following are NOT actions: a phone number offered in case of questions, contact information, opt-out instructions, or any purely informational detail. Attending a scheduled appointment is captured by "type" and "what" — it is not an action_needed. If the message only informs, use null.
Message:
\"\"\"
{message}
\"\"\"""".format(message=message, today=today)
    load_env_file()
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[
            {"role": "user", "content": prompt}
        ],
    )

    # ─── Parse guard ───────────────────────────────────────────
    raw_output = response.content[0].text.strip()
    # Strip a markdown code fence if the model added one despite instructions.
    if raw_output.startswith("```"):
        raw_output = raw_output.split("\n", 1)[-1]   # drop the opening ``` line
        if raw_output.endswith("```"):
            raw_output = raw_output[:-3]
        raw_output = raw_output.strip()
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        raise ValueError("Model did not return valid JSON: " + repr(raw_output))
    # Safety net: if the model returns a bare object instead of an array,
    # wrap it so callers always receive a list (prevents silent mis-iteration).
    if isinstance(parsed, dict):
        parsed = [parsed]
    return parsed

# ─── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    message = """Nick — two things. Please send me the signed W-9 by Wednesday, July 9. Also, we're on for the project kickoff call Thursday, July 10 at 2:00 PM."""
    parsed = extract_commitments(message)
    print("--- PARSED COMMITMENTS ---")
    print(json.dumps(parsed, indent=2))
