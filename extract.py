import anthropic
import json
from datetime import date

# ─── 1. The inputs ──────────────────────────────────────────────
message = """Smith Dental Inst.: Nicholas has an appt on Jul 10 at 1:00 PM. If you have any questions call (813) 555-0055. STOP=EndMsgs"""

today = date.today().isoformat()

prompt = """You are a precise data-extraction tool. Extract commitment information from the message below and return it as a single JSON object.

Today's date is {today}. Use it as the anchor for any relative date reasoning.

Return ONLY the JSON object. No preamble, no explanation, no markdown code fences. Your entire response must be valid, parseable JSON and nothing else.

Use exactly this structure:
{{"type": "...", "what": "...", "date": "...", "time": "...", "action_needed": "..."}}

Field rules:
- "type": one of "appointment", "task", or "reply_needed".
    "appointment" = a scheduled event at a date/time.
    "task" = something to do by a date.
    "reply_needed" = someone is waiting on a response from you.
- "what": a short description of the commitment.
- "date": the relevant date in YYYY-MM-DD format. If the message gives a date with no year, assume the next future occurrence of that date relative to today. If no date is present, use null.
- "time": the time of day in 24-hour HH:MM format. If no time is present, use null.
- "action_needed": a concrete action the recipient is explicitly required to take. Default to null. Only fill this in if the message directly instructs the recipient to DO something. Do not infer, invent, or imply an action that the message does not explicitly state. The following are NOT actions: a phone number offered in case of questions, contact information, opt-out instructions, or any purely informational detail. Attending a scheduled appointment is captured by "type" and "what" — it is not an action_needed. If the message only informs, use null.

Message:
\"\"\"
{message}
\"\"\"""".format(message=message, today=today)

# ─── 2. Send to Claude ──────────────────────────────────────────
client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=300,
    messages=[
        {"role": "user", "content": prompt}
    ],
)

# ─── 3. Get the answer back ─────────────────────────────────────
raw_output = response.content[0].text

# ─── 4. Show it ─────────────────────────────────────────────────
print("--- RAW OUTPUT FROM CLAUDE ---")
print(raw_output)

print("\n--- PARSED AS PYTHON ---")
parsed = json.loads(raw_output)
print(parsed)
