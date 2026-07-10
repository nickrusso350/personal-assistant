import os
import glob
import base64

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_FILE = "token.json"


def get_credentials():
    """Load saved token, or run the browser consent flow on first use."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            cred_files = glob.glob("client_secret_*.json")
            if not cred_files:
                raise SystemExit("No client_secret_*.json found in this folder.")
            flow = InstalledAppFlow.from_client_secrets_file(cred_files[0], SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
    return creds


def find_part(payload, mime_type):
    """Walk the MIME tree and return the base64url data for the first part
    matching mime_type, or None."""
    if payload.get("mimeType") == mime_type:
        data = payload.get("body", {}).get("data")
        if data:
            return data
    for part in payload.get("parts", []):
        found = find_part(part, mime_type)
        if found:
            return found
    return None


def extract_body(payload):
    """Return readable text. Prefer text/plain; fall back to stripped HTML.
    Fail loud if neither exists."""
    plain = find_part(payload, "text/plain")
    if plain:
        return base64.urlsafe_b64decode(plain).decode("utf-8")

    html = find_part(payload, "text/html")
    if html:
        raw = base64.urlsafe_b64decode(html).decode("utf-8")
        return BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True)

    raise SystemExit("No readable text/plain or text/html part found in this message.")


def get_header(payload, name):
    """Return the value of a named header (Subject, From, ...) or None."""
    for header in payload.get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return header.get("value")
    return None


def fetch_one_message(service, query):
    """Find the single newest message matching the query and return its payload."""
    results = service.users().messages().list(
        userId="me", q=query, maxResults=1
    ).execute()
    messages = results.get("messages", [])
    if not messages:
        raise SystemExit(f"No message found for query: {query}")
    msg_id = messages[0]["id"]
    message = service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()
    return message["payload"]


def fetch_recent_messages(service, query, max_results=25):
    """Return a list of recent messages matching the query.

    Each item is a dict: {"subject", "sender", "body", "id"}.
    An unreadable message gets body=None instead of killing the run.
    Returns an empty list if nothing matches.
    """
    results = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    found = results.get("messages", [])

    messages = []
    for item in found:
        message = service.users().messages().get(
            userId="me", id=item["id"], format="full"
        ).execute()
        payload = message["payload"]
        try:
            body = extract_body(payload)
        except SystemExit:
            body = None
        messages.append({
            "subject": get_header(payload, "Subject"),
            "sender": get_header(payload, "From"),
            "body": body,
            "id": item["id"],
        })
    return messages


if __name__ == "__main__":
    query = input("Gmail search query: ").strip()
    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)
    messages = fetch_recent_messages(service, query)

    print(f"----- FOUND {len(messages)} MESSAGE(S) -----")
    for i, msg in enumerate(messages, 1):
        print(f"\n[{i}] From: {msg['sender']}")
        print(f"    Subject: {msg['subject']}")
        if msg["body"] is None:
            print("    Body: <unreadable — no text/plain or text/html part>")
        else:
            preview = " ".join(msg["body"].split())[:100]
            print(f"    Body: {preview}...")
