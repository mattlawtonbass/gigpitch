import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from gigpitch import ai
from gigpitch.ai import CapReached, Claude, cost_of
from gigpitch.dates import add_working_days
from gigpitch.gmail_link import compose_url
from gigpitch.profile import load_profile
from gigpitch.store import Store, name_key
from gigpitch.weekly import run_week, summary

PROFILES = Path(__file__).parent.parent / "profiles"


def usage(inp=1000, out=500, searches=0):
    return SimpleNamespace(
        input_tokens=inp, output_tokens=out, cache_read_input_tokens=0, cache_creation_input_tokens=0,
        server_tool_use=SimpleNamespace(web_search_requests=searches),
    )


def response(text, stop="end_turn", searches=0):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
        usage=usage(searches=searches), model=ai.MODEL,
    )


class FakeClient:
    """Answers each request according to the prompt it was given."""

    def __init__(self):
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        if prompt.startswith("Find up to"):
            return response("Research notes about two venues.", searches=4)
        if prompt.startswith("Turn these research notes"):
            venues = [
                {"name": name, "city": "Hull", "venue_type": "arts centre", "contact_name": "",
                 "email": "", "website": "", "evidence_url": "", "why_fit": "Programmes folk."}
                for name in ("Hull Truck", "The Old Ropery", "Hull Truck")
            ]
            return response(json.dumps({"venues": venues}))
        return response(json.dumps({"subject": "Proposal", "body": "Dear team,\n\nHello.", "decision_for_band": ""}))


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "test.db")
    band_id = store.upsert_band(load_profile(PROFILES / "example-band.yaml"))
    client = FakeClient()
    return store, band_id, Claude(store, client), client


def test_profiles_load():
    for path in PROFILES.glob("*.yaml"):
        profile = load_profile(path)
        assert profile.sender.signature.strip() in profile.as_brief()


def test_working_days_skip_weekends():
    assert add_working_days(date(2026, 10, 23), 5) == date(2026, 10, 30)  # Friday to Friday
    assert add_working_days(date(2026, 10, 24), 1) == date(2026, 10, 26)  # Saturday to Monday


def test_name_key_matches_variants():
    assert name_key("The Brewery Arts Centre") == name_key("Brewery Arts Centre, Kendal")


def test_compose_url_round_trips():
    url = compose_url("a@b.com", "Get Blue: Proposal", "Line one\nLine two & more")
    q = parse_qs(urlparse(url).query)
    assert q["to"] == ["a@b.com"]
    assert q["su"] == ["Get Blue: Proposal"]
    assert q["body"] == ["Line one\nLine two & more"]


def test_cost_includes_searches():
    assert cost_of(usage(1_000_000, 0, 0)) == pytest.approx(ai.PRICE_INPUT)
    assert cost_of(usage(0, 0, 10)) == pytest.approx(10 * ai.PRICE_WEB_SEARCH)


def test_requests_use_fallbacks_and_cap_search(setup):
    store, band_id, claude, client = setup
    run_week(store, claude, band_id, date(2026, 10, 26))
    research = client.requests[0]
    assert research["model"] == ai.MODEL
    assert research["fallbacks"] == "default"
    assert research["tools"][0]["max_uses"] > 0
    extract = client.requests[1]
    assert "tools" not in extract
    assert extract["output_config"]["format"]["type"] == "json_schema"


def test_full_week_cycle(setup):
    store, band_id, claude, _ = setup

    result = run_week(store, claude, band_id, date(2026, 10, 26))
    assert result.leads == ["Hull Truck", "The Old Ropery"]  # duplicate dropped
    assert store.spend_this_month(band_id) > 0

    hull = store.venues(band_id, "Proposed")[0]
    store.set_stage(hull["id"], "Approved")
    result = run_week(store, claude, band_id, date(2026, 10, 26))
    pitch = store.drafts(band_id)[0]
    assert pitch["kind"] == "pitch" and pitch["venue_id"] == hull["id"]
    assert "Open in Gmail" in summary(store, band_id, result)

    store.mark_sent(pitch["id"], date(2026, 10, 26))
    v = store.venue(hull["id"])
    assert v["stage"] == "Sent" and v["chase_due"] == "2026-11-02"

    run_week(store, claude, band_id, date(2026, 10, 30))
    assert not [d for d in store.drafts(band_id) if d["kind"] == "chase"]  # not due yet

    run_week(store, claude, band_id, date(2026, 11, 2))
    chase = [d for d in store.drafts(band_id) if d["kind"] == "chase"][0]
    store.mark_sent(chase["id"], date(2026, 11, 2))
    assert store.venue(hull["id"])["stage"] == "Chased"

    run_week(store, claude, band_id, date(2026, 11, 9))
    assert len([d for d in store.drafts(band_id, "ready") if d["kind"] == "chase"]) == 0  # only one chase

    store.add_reply(band_id, hull["id"], "We'd love to talk. What is your fee?", "prog@hulltruck.example")
    run_week(store, claude, band_id, date(2026, 11, 9))
    assert store.venue(hull["id"])["stage"] == "Replied"
    assert [d for d in store.drafts(band_id) if d["kind"] == "reply"]


def test_band_cap_stops_calls(setup, monkeypatch):
    store, band_id, claude, client = setup
    monkeypatch.setattr(ai, "BAND_MONTHLY_CAP", 1.0)
    store.record_usage(band_id, "earlier", ai.MODEL, 0, 0, 0, 0, 0, 0.75)
    with pytest.raises(CapReached):
        claude.call(band_id, "test", "system", "prompt")
    result = run_week(store, claude, band_id, date(2026, 10, 26))
    assert result.problems and result.problems[0].startswith("Stopped early")
    assert client.requests == []


def test_global_cap_covers_all_bands(setup, monkeypatch):
    store, band_id, claude, _ = setup
    other = store.upsert_band(load_profile(PROFILES / "get-blue.yaml"))
    monkeypatch.setattr(ai, "GLOBAL_MONTHLY_CAP", 2.0)
    store.record_usage(other, "earlier", ai.MODEL, 0, 0, 0, 0, 0, 1.8)
    with pytest.raises(CapReached, match="All bands"):
        claude.call(band_id, "test", "system", "prompt")


def test_pause_turn_resumes(setup):
    store, band_id, claude, client = setup
    replies = iter([response("", stop="pause_turn"), response("done")])
    client.beta.messages.create = lambda **kw: (client.requests.append(kw), next(replies))[1]
    assert claude.call(band_id, "test", "system", "prompt").text == "done"
    assert client.requests[1]["messages"][1]["role"] == "assistant"
