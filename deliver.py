from digest import run_digest
from send_sms import send_sms, RECIPIENT

if __name__ == "__main__":
    text = run_digest()
    send_sms(text)
    print(f"Sent to {RECIPIENT}")
