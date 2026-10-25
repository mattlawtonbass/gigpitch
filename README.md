# Gigpitch

Weekly venue leads and ready-to-send pitches for bands. Each week it:

1. Finds up to 5 new venues that fit the band's profile, checked on each venue's own website.
2. Writes a pitch for each lead the band approves.
3. Writes one chase five working days after a pitch is sent.
4. Answers venue replies, with a holding reply whenever fees, dates or terms come up.

The band sends everything from their own Gmail through an "Open in Gmail" link. Gigpitch never sends email.

This is week 1 of the beta: the weekly job and a command line. The web app, inbound reply forwarding and summary emails come next.

## Setup

```bash
pip install -e '.[dev]'
export ANTHROPIC_API_KEY=...        # never commit this
gigpitch add-band profiles/example-band.yaml
```

## Weekly use

```bash
gigpitch run-week the-tidelines        # finds leads, writes drafts, saves outbox/<band>-<date>.txt
gigpitch approve the-tidelines 3 5     # approve proposed venues by id
gigpitch reject the-tidelines 4
gigpitch run-week the-tidelines        # writes pitches for approved venues
gigpitch show the-tidelines            # drafts with Open in Gmail links
gigpitch mark-sent the-tidelines 7     # starts the 5-working-day chase timer
gigpitch add-reply the-tidelines 3 --from-email prog@venue.example --file reply.txt
gigpitch spend                         # this month's API spend against the caps
```

## Spending caps

Every API call is costed and logged. Before each call Gigpitch checks two monthly caps (USD):

| Setting | Default | What it limits |
| --- | --- | --- |
| `GIGPITCH_BAND_MONTHLY_CAP_USD` | 5 | One band's spend in a calendar month |
| `GIGPITCH_GLOBAL_MONTHLY_CAP_USD` | 25 | All bands together |
| `GIGPITCH_CALL_RESERVE_USD` | 0.50 | Headroom kept so one more call can't overshoot a cap |

When a cap is reached the run stops and the summary says so. Replies are handled first, so they are the last thing to be cut.

Set a hard limit in the Claude Console as well, as a backstop: Settings > Limits, then set a monthly spend limit. Making a separate workspace for Gigpitch with its own limit, and creating the API key inside it, keeps this spend apart from anything else on the account.

## Tests

```bash
pytest
```

The tests use a fake API client, so they cost nothing.
