"""SQLite storage for bands, the venue pipeline, drafts, venue replies and API usage."""

import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from .dates import add_working_days
from .profile import BandProfile

STAGES = ["Proposed", "Approved", "Sent", "Chased", "Replied", "Hold", "Booked", "Closed"]
CHASE_AFTER_WORKING_DAYS = 5
VENUE_FIELDS = {
    "city", "venue_type", "contact_name", "email", "website", "stage", "why_fit", "evidence_url",
    "date_contacted", "chase_due", "last_contact", "fee_agreed", "gig_date", "notes",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS bands (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS venues (
    id INTEGER PRIMARY KEY,
    band_id INTEGER NOT NULL REFERENCES bands(id),
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    city TEXT DEFAULT '',
    venue_type TEXT DEFAULT '',
    contact_name TEXT DEFAULT '',
    email TEXT DEFAULT '',
    website TEXT DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'Proposed',
    why_fit TEXT DEFAULT '',
    evidence_url TEXT DEFAULT '',
    date_contacted TEXT,
    chase_due TEXT,
    last_contact TEXT,
    fee_agreed TEXT DEFAULT '',
    gig_date TEXT,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (band_id, name_key)
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    band_id INTEGER NOT NULL REFERENCES bands(id),
    venue_id INTEGER NOT NULL REFERENCES venues(id),
    kind TEXT NOT NULL CHECK (kind IN ('pitch', 'chase', 'reply')),
    to_email TEXT DEFAULT '',
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    decision_for_band TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'sent', 'discarded')),
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY,
    band_id INTEGER NOT NULL REFERENCES bands(id),
    venue_id INTEGER REFERENCES venues(id),
    from_email TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    body TEXT NOT NULL,
    received_at TEXT NOT NULL,
    handled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY,
    band_id INTEGER REFERENCES bands(id),
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    web_searches INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def name_key(name: str) -> str:
    """Normalise a venue name so 'The Brewery Arts Centre' and 'Brewery Arts Centre, Kendal' match."""
    key = name.lower().split(",")[0]
    key = re.sub(r"^the\s+", "", key)
    return re.sub(r"[^a-z0-9]", "", key)


class Store:
    def __init__(self, path: str | Path = "gigpitch.db"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # Bands

    def upsert_band(self, profile: BandProfile) -> int:
        self.db.execute(
            "INSERT INTO bands (slug, profile_json, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET profile_json = excluded.profile_json",
            (profile.slug, profile.model_dump_json(), _now()),
        )
        self.db.commit()
        return self.band_id(profile.slug)

    def band_id(self, slug: str) -> int:
        row = self.db.execute("SELECT id FROM bands WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            raise KeyError(f"No band called {slug!r}. Add it with: gigpitch add-band <profile.yaml>")
        return row["id"]

    def profile(self, band_id: int) -> BandProfile:
        row = self.db.execute("SELECT profile_json FROM bands WHERE id = ?", (band_id,)).fetchone()
        return BandProfile.model_validate(json.loads(row["profile_json"]))

    # Venues

    def add_venue(self, band_id: int, name: str, **fields) -> int | None:
        """Add a venue. Returns None if the band already has it."""
        unknown = set(fields) - VENUE_FIELDS
        if unknown:
            raise ValueError(f"Unknown venue fields: {', '.join(sorted(unknown))}")
        cols = ["band_id", "name", "name_key", "created_at", *fields]
        vals = [band_id, name, name_key(name), _now(), *fields.values()]
        try:
            cur = self.db.execute(
                f"INSERT INTO venues ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals
            )
        except sqlite3.IntegrityError:
            return None
        self.db.commit()
        return cur.lastrowid

    def venue(self, venue_id: int) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM venues WHERE id = ?", (venue_id,)).fetchone()

    def venues(self, band_id: int, stage: str | None = None) -> list[sqlite3.Row]:
        if stage:
            return self.db.execute(
                "SELECT * FROM venues WHERE band_id = ? AND stage = ? ORDER BY id", (band_id, stage)
            ).fetchall()
        return self.db.execute("SELECT * FROM venues WHERE band_id = ? ORDER BY id", (band_id,)).fetchall()

    def known_venue_names(self, band_id: int) -> list[str]:
        return [r["name"] for r in self.venues(band_id)]

    def set_stage(self, venue_id: int, stage: str, **fields) -> None:
        if stage not in STAGES:
            raise ValueError(f"Stage must be one of {', '.join(STAGES)}")
        unknown = set(fields) - VENUE_FIELDS
        if unknown:
            raise ValueError(f"Unknown venue fields: {', '.join(sorted(unknown))}")
        sets = ["stage = ?", *(f"{k} = ?" for k in fields)]
        self.db.execute(
            f"UPDATE venues SET {', '.join(sets)} WHERE id = ?", (stage, *fields.values(), venue_id)
        )
        self.db.commit()

    def approved_without_pitch(self, band_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT v.* FROM venues v WHERE v.band_id = ? AND v.stage = 'Approved' AND NOT EXISTS "
            "(SELECT 1 FROM drafts d WHERE d.venue_id = v.id AND d.kind = 'pitch' AND d.status != 'discarded')",
            (band_id,),
        ).fetchall()

    def chases_due(self, band_id: int, today: date) -> list[sqlite3.Row]:
        """Venues sent a pitch whose one chase is due and not yet drafted."""
        return self.db.execute(
            "SELECT v.* FROM venues v WHERE v.band_id = ? AND v.stage = 'Sent' AND v.chase_due <= ? "
            "AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.venue_id = v.id AND d.kind = 'chase' "
            "AND d.status != 'discarded')",
            (band_id, today.isoformat()),
        ).fetchall()

    # Drafts

    def add_draft(self, band_id: int, venue_id: int, kind: str, subject: str, body: str,
                  to_email: str = "", decision_for_band: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO drafts (band_id, venue_id, kind, to_email, subject, body, decision_for_band, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (band_id, venue_id, kind, to_email, subject, body, decision_for_band, _now()),
        )
        self.db.commit()
        return cur.lastrowid

    def drafts(self, band_id: int, status: str = "ready") -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT d.*, v.name AS venue_name FROM drafts d JOIN venues v ON v.id = d.venue_id "
            "WHERE d.band_id = ? AND d.status = ? ORDER BY d.id",
            (band_id, status),
        ).fetchall()

    def mark_sent(self, draft_id: int, today: date) -> None:
        """Record that the band sent a draft, and move its venue on. A pitch starts the chase timer."""
        d = self.db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if d is None:
            raise KeyError(f"No draft {draft_id}")
        self.db.execute("UPDATE drafts SET status = 'sent', sent_at = ? WHERE id = ?", (_now(), draft_id))
        day = today.isoformat()
        if d["kind"] == "pitch":
            due = add_working_days(today, CHASE_AFTER_WORKING_DAYS).isoformat()
            self.set_stage(d["venue_id"], "Sent", date_contacted=day, last_contact=day, chase_due=due)
        elif d["kind"] == "chase":
            self.set_stage(d["venue_id"], "Chased", last_contact=day, chase_due=None)
        else:
            self.db.execute("UPDATE venues SET last_contact = ? WHERE id = ?", (day, d["venue_id"]))
        self.db.commit()

    def discard_draft(self, draft_id: int) -> None:
        self.db.execute("UPDATE drafts SET status = 'discarded' WHERE id = ?", (draft_id,))
        self.db.commit()

    # Replies

    def add_reply(self, band_id: int, venue_id: int | None, body: str,
                  from_email: str = "", subject: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO replies (band_id, venue_id, from_email, subject, body, received_at) VALUES (?, ?, ?, ?, ?, ?)",
            (band_id, venue_id, from_email, subject, body, _now()),
        )
        self.db.commit()
        return cur.lastrowid

    def unhandled_replies(self, band_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM replies WHERE band_id = ? AND handled = 0 ORDER BY id", (band_id,)
        ).fetchall()

    def mark_reply_handled(self, reply_id: int) -> None:
        self.db.execute("UPDATE replies SET handled = 1 WHERE id = ?", (reply_id,))
        self.db.commit()

    # Usage

    def record_usage(self, band_id: int | None, purpose: str, model: str, input_tokens: int,
                     output_tokens: int, cache_read_tokens: int, cache_write_tokens: int,
                     web_searches: int, cost_usd: float) -> None:
        self.db.execute(
            "INSERT INTO usage (band_id, purpose, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, web_searches, cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (band_id, purpose, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
             web_searches, cost_usd, _now()),
        )
        self.db.commit()

    def spend_this_month(self, band_id: int | None = None, today: date | None = None) -> float:
        """Spend in USD this calendar month, for one band or (band_id=None) for everyone."""
        month = (today or date.today()).strftime("%Y-%m")
        sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE substr(created_at, 1, 7) = ?"
        args: tuple = (month,)
        if band_id is not None:
            sql += " AND band_id = ?"
            args += (band_id,)
        return float(self.db.execute(sql, args).fetchone()[0])
