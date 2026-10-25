from urllib.parse import urlencode

# Gmail truncates very long compose URLs; past this we warn so the band can paste instead.
SAFE_URL_LENGTH = 7000


def compose_url(to: str, subject: str, body: str) -> str:
    """A link that opens a filled-in email in whichever Gmail account the band is signed in to."""
    query = urlencode({"view": "cm", "fs": "1", "to": to or "", "su": subject, "body": body})
    return f"https://mail.google.com/mail/?{query}"


def is_too_long(url: str) -> bool:
    return len(url) > SAFE_URL_LENGTH
