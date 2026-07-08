import anthropic
import json
from datetime import date

# ─── The extraction brain ───────────────────────────────────────
def extract_commitment(message):
    """Take one message string, return the commitment as a dict.
    Returns a dict with keys: type, what, date, time, action_needed.
    """
    today = date.today().isoformat()
    prompt = """You are a precise data-extraction tool. Extract commitment information from the message below and return it as a single JSON object.
Today's date is {today}. Use it as the anchor for any relative date reasoning.
Return ONLY the JSON object. No preamble, no explanation, no markdown code fences. Your entire response must be valid, parseable JSON and nothing else.
Use exactly this structure:
{{"type": "...", "what": "...", "date": "...", "time": "...", "action_needed": "..."}}
Field rules:
- "type": one of "none", "appointment", "task", or "reply_needed".
    Most messages contain NO commitment — promotions, newsletters,
    notifications, receipts, and general updates are all "none".

    OWNERSHIP TEST — a commitment must be an obligation YOU personally own.
    Some messages use task-shaped or reply-shaped language but the obligation
    actually sits with the SENDER, not you. These are "none":
      - A company soliciting a favor — "review your purchase", "rate your
        order", "take our survey", "leave feedback" — is "none", not a task.
        You never committed to it.
      - A courtesy acknowledgment where the other party holds the next move —
        "thanks, we'll take a look and get back to you", "keep us posted if
        anything changes", "we'll be in touch" — is "none", not reply_needed.
        They owe the next step, not you.

    Only use a type other than "none" when the message explicitly states a
    commitment AND the recipient owns it:
    "appointment" = a scheduled event at a date/time.
    "task" = something YOU need to do by a date — that you committed to or are
        responsible for, not a favor a company is requesting.
    "reply_needed" = someone is specifically waiting on and requesting a
        response FROM YOU now (for example, "please reply to confirm") — not a
        standing courtesy where the sender will follow up.
    Do not infer or invent a commitment that the message does not explicitly
    state. When in doubt, use "none".
- If "type" is "none", every other field must be null:
    {{"type": "none", "what": null, "date": null, "time": null, "action_needed": null}}
- "what": a short description of the commitment.
- "date": the relevant date in YYYY-MM-DD format. If the message gives a date with no year, assume the next future occurrence of that date relative to today. If no date is present, use null.
- "time": the time of day in 24-hour HH:MM format. If no time is present, use null.
- "action_needed": a concrete action the recipient is explicitly required to take. Default to null. Only fill this in if the message directly instructs the recipient to DO something. Do not infer, invent, or imply an action that the message does not explicitly state. The following are NOT actions: a phone number offered in case of questions, contact information, opt-out instructions, or any purely informational detail. Attending a scheduled appointment is captured by "type" and "what" — it is not an action_needed. If the message only informs, use null.
Message:
\"\"\"
{message}
\"\"\"""".format(message=message, today=today)
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[
            {"role": "user", "content": prompt}
        ],
    )
    raw_output = response.content[0].text
    return json.loads(raw_output)

# ─── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    message = """Smith Dental: please reply to confirm your Jul 10 appointment."""
    parsed = extract_commitment(message)
    print("--- PARSED COMMITMENT ---")
    print(parsed)
