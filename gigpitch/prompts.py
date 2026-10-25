"""Prompts and output schemas. The rules here are the ones that work for Get Blue, made band-neutral."""

RULES = """You are a booking assistant for one band. You prepare work; the band leader approves and sends everything.

Rules:
- Never quote a fee, offer or confirm a date, or accept terms. If a venue asks about any of these, write a holding reply and say what the band leader needs to decide.
- Never invent facts about the band, its audiences, reviews, ticket sales or past gigs. Use only the profile below.
- Never contact anyone on the do-not-contact list.
- Auto-replies and out-of-office messages are not interest.
- Write in UK English unless the profile says otherwise.
- Emails are plain text, short and formal, written by the band leader in the first person, ending with the band's signature exactly as given.
- Link the EPK when there is one. Do not attach anything."""


def system_prompt(profile_brief: str) -> str:
    return f"{RULES}\n\nBand profile:\n{profile_brief}"


LEAD_RESEARCH = """Find up to {n} new venues that could book this band.

They must match the target region and venue types in the profile, and must currently programme live music similar to this band's. Check each one on the venue's own website. Leave out any venue that already programmes a very similar act soon, and any venue on the do-not-contact or avoid lists.

The band already knows these venues, so leave them out:
{known}

For each venue give: the name, the town or city, the type of venue, the booking or programming contact (name and email) if the website shows one, the website, the page that shows it fits, and one sentence on why it fits this band."""

LEAD_EXTRACT = """Turn these research notes into a list of venues. Use only what the notes say; leave a field empty if the notes don't give it.

Notes:
{notes}"""

LEADS_SCHEMA = {
    "type": "object",
    "properties": {
        "venues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "city": {"type": "string"},
                    "venue_type": {"type": "string"},
                    "contact_name": {"type": "string"},
                    "email": {"type": "string"},
                    "website": {"type": "string"},
                    "evidence_url": {"type": "string"},
                    "why_fit": {"type": "string"},
                },
                "required": ["name", "city", "venue_type", "contact_name", "email", "website",
                             "evidence_url", "why_fit"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["venues"],
    "additionalProperties": False,
}

EMAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "decision_for_band": {
            "type": "string",
            "description": "What the band leader must decide before or after sending; empty if nothing.",
        },
    },
    "required": ["subject", "body", "decision_for_band"],
    "additionalProperties": False,
}

PITCH = """Write a first pitch email to this venue, proposing that it books the band.

Venue: {name}, {city} ({venue_type})
Contact: {contact_name}
Why it fits: {why_fit}
Notes: {notes}

Address the contact by name if known, otherwise use "Dear Programming Team". Mention why this venue suits the band in one sentence. Suggest a show in the band's current focus period if the profile gives one, without naming a fee."""

CHASE = """Write one short, polite follow-up to the pitch below, which has had no reply. This is the only chase, so don't promise another.

Venue: {name}, {city}
Contact: {contact_name}
Original subject: {subject}
Original email:
{body}"""

REPLY = """A venue has replied. Write the band leader's reply.

If the venue asks about fees, dates, terms, contracts or anything the profile doesn't answer, write a holding reply that thanks them and says the band leader will come back shortly, and put the question in decision_for_band. If the message is an auto-reply, write nothing in body and say so in decision_for_band.

Venue: {name}, {city}
Their email:
Subject: {subject}
{body}"""
