import os
import glob
import json
import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
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
            if len(cred_files) > 1:
                raise SystemExit(
                    f"Multiple client_secret_*.json found: {cred_files}. "
                    "Leave exactly one in this folder."
                )
            flow = InstalledAppFlow.from_client_secrets_file(cred_files[0], SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
    return creds


if __name__ == "__main__":
    creds = get_credentials()
    service = build("calendar", "v3", credentials=creds)

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=10,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    events = results.get("items", [])

    if not events:
        print("No upcoming events found.")
        raise SystemExit(0)

    print(f"----- FOUND {len(events)} EVENT(S) -----")
    for event in events:
        print("-" * 40)
        print(json.dumps(event, indent=2))
