"""A band's weekly run: new leads, pitches for approved leads, due chases, and answers to venue replies."""

from dataclasses import dataclass, field
from datetime import date

from . import prompts
from .ai import CapReached, Claude, Refused
from .gmail_link import compose_url, is_too_long
from .store import Store

WEB_SEARCHES_PER_RUN = 15


@dataclass
class WeekResult:
    leads: list[str] = field(default_factory=list)
    drafts: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


def run_week(store: Store, claude: Claude, band_id: int, today: date | None = None) -> WeekResult:
    today = today or date.today()
    profile = store.profile(band_id)
    system = prompts.system_prompt(profile.as_brief())
    result = WeekResult()

    steps = [
        lambda: _replies(store, claude, band_id, system, result),
        lambda: _chases(store, claude, band_id, system, today, result),
        lambda: _pitches(store, claude, band_id, system, result),
        lambda: _leads(store, claude, band_id, system, profile.leads_per_week, result),
    ]
    # Replies first: they matter most if the cap runs out part way through.
    for step in steps:
        try:
            step()
        except CapReached as e:
            result.problems.append(f"Stopped early: {e}")
            break
    return result


def _replies(store, claude, band_id, system, result):
    for r in store.unhandled_replies(band_id):
        if r["venue_id"] is None:
            result.problems.append(f"Reply {r['id']} from {r['from_email']} isn't matched to a venue yet")
            continue
        v = store.venue(r["venue_id"])
        try:
            reply = claude.call(band_id, "reply", system, prompts.REPLY.format(
                name=v["name"], city=v["city"], subject=r["subject"], body=r["body"]), schema=prompts.EMAIL_SCHEMA)
        except Refused as e:
            result.problems.append(str(e))
            continue
        result.cost_usd += reply.cost_usd
        d = reply.data
        if d["body"].strip():
            result.drafts.append(store.add_draft(band_id, v["id"], "reply", d["subject"], d["body"],
                                                 r["from_email"] or v["email"], d["decision_for_band"]))
        elif d["decision_for_band"]:
            result.problems.append(f"{v['name']}: {d['decision_for_band']}")
        if v["stage"] in ("Sent", "Chased"):
            store.set_stage(v["id"], "Replied", last_contact=r["received_at"][:10])
        store.mark_reply_handled(r["id"])


def _chases(store, claude, band_id, system, today, result):
    for v in store.chases_due(band_id, today):
        pitch = store.db.execute(
            "SELECT subject, body FROM drafts WHERE venue_id = ? AND kind = 'pitch' AND status = 'sent' "
            "ORDER BY id DESC LIMIT 1", (v["id"],)).fetchone()
        if pitch is None:
            continue
        try:
            reply = claude.call(band_id, "chase", system, prompts.CHASE.format(
                name=v["name"], city=v["city"], contact_name=v["contact_name"] or "unknown",
                subject=pitch["subject"], body=pitch["body"]), schema=prompts.EMAIL_SCHEMA, effort="low")
        except Refused as e:
            result.problems.append(str(e))
            continue
        result.cost_usd += reply.cost_usd
        d = reply.data
        result.drafts.append(store.add_draft(band_id, v["id"], "chase", d["subject"], d["body"], v["email"],
                                             d["decision_for_band"]))


def _pitches(store, claude, band_id, system, result):
    for v in store.approved_without_pitch(band_id):
        try:
            reply = claude.call(band_id, "pitch", system, prompts.PITCH.format(
                name=v["name"], city=v["city"], venue_type=v["venue_type"],
                contact_name=v["contact_name"] or "unknown", why_fit=v["why_fit"], notes=v["notes"] or "none"),
                schema=prompts.EMAIL_SCHEMA)
        except Refused as e:
            result.problems.append(str(e))
            continue
        result.cost_usd += reply.cost_usd
        d = reply.data
        result.drafts.append(store.add_draft(band_id, v["id"], "pitch", d["subject"], d["body"], v["email"],
                                             d["decision_for_band"]))


def _leads(store, claude, band_id, system, n, result):
    if n <= 0:
        return
    known = store.known_venue_names(band_id)
    try:
        notes = claude.call(band_id, "lead research", system, prompts.LEAD_RESEARCH.format(
            n=n, known="\n".join(f"- {k}" for k in known) or "- none yet"), web_search=WEB_SEARCHES_PER_RUN)
        result.cost_usd += notes.cost_usd
        found = claude.call(band_id, "lead extract", system, prompts.LEAD_EXTRACT.format(notes=notes.text),
                            schema=prompts.LEADS_SCHEMA, effort="low")
    except Refused as e:
        result.problems.append(str(e))
        return
    result.cost_usd += found.cost_usd
    for lead in found.data["venues"][:n]:
        name = lead.pop("name").strip()
        if name and store.add_venue(band_id, name, stage="Proposed", **lead):
            result.leads.append(name)


def summary(store: Store, band_id: int, result: WeekResult) -> str:
    """The weekly summary email, as plain text."""
    profile = store.profile(band_id)
    lines = [f"{profile.name}: this week", ""]
    proposed = store.venues(band_id, "Proposed")
    if proposed:
        lines.append(f"New leads to approve or reject ({len(proposed)}):")
        for v in proposed:
            lines.append(f"  [{v['id']}] {v['name']}, {v['city']}: {v['why_fit']}")
            if v["evidence_url"]:
                lines.append(f"      {v['evidence_url']}")
        lines.append("")
    drafts = store.drafts(band_id)
    if drafts:
        lines.append(f"Emails ready to send ({len(drafts)}):")
        for d in drafts:
            url = compose_url(d["to_email"], d["subject"], d["body"])
            lines.append(f"  [{d['id']}] {d['kind'].title()} to {d['venue_name']}: {d['subject']}")
            if d["decision_for_band"]:
                lines.append(f"      Your call: {d['decision_for_band']}")
            if not d["to_email"]:
                lines.append("      No email address found; add the recipient in Gmail.")
            if is_too_long(url):
                lines.append("      Too long for a Gmail link; copy it from the app instead.")
            else:
                lines.append(f"      Open in Gmail: {url}")
        lines.append("")
    for p in result.problems:
        lines.append(f"Note: {p}")
    if len(lines) == 2:
        lines.append("Nothing new this week.")
    return "\n".join(lines)
