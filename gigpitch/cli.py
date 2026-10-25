"""Command line for week 1: run a band's week and work through the results before the web app exists."""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from .ai import BAND_MONTHLY_CAP, GLOBAL_MONTHLY_CAP, Claude
from .gmail_link import compose_url
from .profile import load_profile
from .store import STAGES, Store
from .weekly import run_week, summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gigpitch")
    p.add_argument("--db", default=os.environ.get("GIGPITCH_DB", "gigpitch.db"))
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add-band", help="add or update a band from a profile file")
    a.add_argument("profile")

    r = sub.add_parser("run-week", help="run a band's weekly job and write the summary")
    r.add_argument("band")
    r.add_argument("--outbox", default="outbox")

    s = sub.add_parser("show", help="print a band's pipeline and ready drafts")
    s.add_argument("band")

    for name, help_text in (("approve", "approve proposed venues"), ("reject", "reject proposed venues")):
        c = sub.add_parser(name, help=help_text)
        c.add_argument("band")
        c.add_argument("venue_ids", nargs="+", type=int)

    st = sub.add_parser("stage", help="set a venue's stage by hand")
    st.add_argument("band")
    st.add_argument("venue_id", type=int)
    st.add_argument("stage", choices=STAGES)

    ms = sub.add_parser("mark-sent", help="record that drafts were sent")
    ms.add_argument("band")
    ms.add_argument("draft_ids", nargs="+", type=int)

    ar = sub.add_parser("add-reply", help="add a venue's reply, pasted from a file or stdin")
    ar.add_argument("band")
    ar.add_argument("venue_id", type=int)
    ar.add_argument("--from-email", default="")
    ar.add_argument("--subject", default="")
    ar.add_argument("--file", help="read the reply text from this file instead of stdin")

    sub.add_parser("spend", help="show this month's API spend against the caps")

    args = p.parse_args(argv)
    store = Store(args.db)

    if args.cmd == "add-band":
        profile = load_profile(args.profile)
        store.upsert_band(profile)
        print(f"Saved {profile.name} as '{profile.slug}'")
        return 0

    if args.cmd == "spend":
        print(f"All bands: ${store.spend_this_month():.2f} of ${GLOBAL_MONTHLY_CAP:.2f}")
        for row in store.db.execute("SELECT id, slug FROM bands ORDER BY slug"):
            print(f"  {row['slug']}: ${store.spend_this_month(row['id']):.2f} of ${BAND_MONTHLY_CAP:.2f}")
        return 0

    band_id = store.band_id(args.band)

    if args.cmd == "run-week":
        result = run_week(store, Claude(store), band_id)
        text = summary(store, band_id, result)
        out = Path(args.outbox)
        out.mkdir(exist_ok=True)
        path = out / f"{args.band}-{date.today().isoformat()}.txt"
        path.write_text(text, encoding="utf-8")
        print(text)
        print(f"\nCost this run: ${result.cost_usd:.2f}. Saved to {path}")
        return 0

    if args.cmd == "show":
        for v in store.venues(band_id):
            due = f", chase due {v['chase_due']}" if v["chase_due"] else ""
            print(f"[{v['id']}] {v['stage']:<9} {v['name']}, {v['city']}{due}")
        drafts = store.drafts(band_id)
        if drafts:
            print("\nReady to send:")
        for d in drafts:
            print(f"\n[{d['id']}] {d['kind']} to {d['venue_name']} <{d['to_email']}>\nSubject: {d['subject']}\n\n{d['body']}")
            if d["decision_for_band"]:
                print(f"\nYour call: {d['decision_for_band']}")
            print(f"\nOpen in Gmail: {compose_url(d['to_email'], d['subject'], d['body'])}")
        return 0

    if args.cmd in ("approve", "reject"):
        new_stage = "Approved" if args.cmd == "approve" else "Closed"
        for vid in args.venue_ids:
            v = store.venue(vid)
            if v is None or v["band_id"] != band_id or v["stage"] != "Proposed":
                print(f"Skipped {vid}: not a proposed venue for this band", file=sys.stderr)
                continue
            store.set_stage(vid, new_stage)
            print(f"{v['name']}: {new_stage}")
        return 0

    if args.cmd == "stage":
        store.set_stage(args.venue_id, args.stage)
        return 0

    if args.cmd == "mark-sent":
        for did in args.draft_ids:
            store.mark_sent(did, date.today())
            print(f"Draft {did} marked sent")
        return 0

    if args.cmd == "add-reply":
        body = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
        store.add_reply(band_id, args.venue_id, body, args.from_email, args.subject)
        print("Reply saved; it will be answered on the next run")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
