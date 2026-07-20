import os

from env_loader import load_env_file

MESSAGING_SERVICE_SID = "MG798b0a646358ad9fceae5fc7090dff02"
RECIPIENT = "+18133137914"

def send_sms(text):
    """Send an SMS via Twilio's Messaging Service.

    Reads TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN from the environment and
    raises RuntimeError naming the missing variable before importing or
    constructing any Twilio client. Never truncates: text longer than 1600
    characters raises RuntimeError. Never prints or logs the credentials.

    Prints the returned message's sid and status. Returns None on success."""
    load_env_file()
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    if not account_sid:
        raise RuntimeError("Missing environment variable: TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not auth_token:
        raise RuntimeError("Missing environment variable: TWILIO_AUTH_TOKEN")

    if len(text) > 1600:
        raise RuntimeError(f"Message too long: {len(text)} characters (max 1600)")

    from twilio.rest import Client

    client = Client(account_sid, auth_token)
    try:
        message = client.messages.create(
            body=text,
            messaging_service_sid=MESSAGING_SERVICE_SID,
            to=RECIPIENT,
        )
    except Exception as e:
        raise RuntimeError(f"Twilio failed to send message: {e}")

    print(f"sid: {message.sid}, status: {message.status}")
    return None
