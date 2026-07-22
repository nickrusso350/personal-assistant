import datetime

from googleapiclient.discovery import build
from fetch_gmail import get_credentials


def create_event(summary, date_str, time_str=None):
    """Create an event on the primary Google Calendar and return its event id.

    Timed (time_str given): starts at date_str + time_str in the machine's local
    time zone, ends one hour later. All-day (no time_str): a single-day all-day
    event whose exclusive end.date is the day after date_str.

    This function performs the only write in the codebase."""
    creds = get_credentials()
    service = build("calendar", "v3", credentials=creds)

    if time_str:
        start = datetime.datetime.fromisoformat(f"{date_str}T{time_str}").astimezone()
        end = start + datetime.timedelta(hours=1)
        body = {
            "summary": summary,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
    else:
        start_date = datetime.date.fromisoformat(date_str)
        end_date = start_date + datetime.timedelta(days=1)
        body = {
            "summary": summary,
            "start": {"date": start_date.isoformat()},
            "end": {"date": end_date.isoformat()},
        }

    event = service.events().insert(calendarId="primary", body=body).execute()
    return event["id"]
