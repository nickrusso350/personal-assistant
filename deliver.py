from digest import run_digest
from send_imessage import send_imessage, RECIPIENT

if __name__ == "__main__":
    text = run_digest()
    send_imessage(text)
    print(f"Sent to {RECIPIENT}")
