import datetime
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from fetch_gmail import get_credentials

DEFAULT_TZ = "America/New_York"


def _parse_endpoint(endpoint, default_tz):
    """Parse an event start/end object.

    Returns (value, all_day) where value is a datetime (converted to the
    event's timeZone) for timed events, or a date for all-day events.
    """
    if endpoint.get("dateTime"):
        raw = endpoint["dateTime"].replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(raw)
        tz = ZoneInfo(endpoint.get("timeZone") or default_tz)
        return dt.astimezone(tz), False
    return datetime.date.fromisoformat(endpoint["date"]), True


def fetch_upcoming_events(days_ahead=7):
    """Return upcoming events from the primary calendar over the next
    days_ahead days.

    Each item is a dict: {id, summary, start_local, end_local, all_day}.
    Timed events carry datetime objects (in the event's timeZone, falling
    back to America/New_York); all-day events carry date objects.
    Returns an empty list if nothing matches.
    """
    creds = get_credentials()
    service = build("calendar", "v3", credentials=creds)

    now = datetime.datetime.now(datetime.timezone.utc)
    time_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = (now + datetime.timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ")

    results = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute()
    events = results.get("items", [])

    parsed = []
    for event in events:
        start_local, all_day = _parse_endpoint(event.get("start", {}), DEFAULT_TZ)
        end_local, _ = _parse_endpoint(event.get("end", {}), DEFAULT_TZ)
        parsed.append({
            "id": event.get("id"),
            "summary": event.get("summary", "(untitled)"),
            "start_local": start_local,
            "end_local": end_local,
            "all_day": all_day,
        })
    return parsed


if __name__ == "__main__":
    events = fetch_upcoming_events()
    print(f"----- {len(events)} UPCOMING EVENT(S) -----")
    for event in events:
        if event["all_day"]:
            date_str = event["start_local"].isoformat()
            time_str = "all day"
        else:
            date_str = event["start_local"].date().isoformat()
            time_str = (
                f"{event['start_local'].strftime('%H:%M')}"
                f"-{event['end_local'].strftime('%H:%M')}"
            )
        print(f"{date_str}  {time_str}  {event['summary']}")
