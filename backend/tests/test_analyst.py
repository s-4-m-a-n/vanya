import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.adapters.llm import LLMError
from app.cli import generate_reports
from app.models import AnalystQuery, Report, Role, Taxon, WatchlistEntry
from app.services.analyst import AnalystEngine, validate_plan
from tests.helpers import LONDON_SQUARE, add_org_with_region, make_verified, square

TODAY = date.today()
NOW = datetime.now(UTC)


def taxon(db, name) -> Taxon:
    return db.scalar(select(Taxon).where(Taxon.scientific_name == name))


def plan(**overrides):
    raw = {
        "intent": "counts",
        "group_by": "none",
        "start": (TODAY - timedelta(days=365)).isoformat(),
        "end": TODAY.isoformat(),
        "scientific_names": [],
        "families": [],
        "classes": [],
        "groups": [],
        "iucn_statuses": [],
        "watchlist": "none",
        "regions": [],
        "limit": 10,
        "interpretation": "test",
    }
    raw.update(overrides)
    return raw


# --- Plan validation ---------------------------------------------------------------------


def test_plan_validation_bounds_everything():
    regions = {"Greater London": 1, "Epping Forest": 2}
    p = validate_plan(
        plan(
            intent="drop table",
            group_by="password",
            start="1900-01-01",
            end="2999-01-01",
            regions=["greater london", "Atlantis"],
            groups=["bird", "fungus"],
            iucn_statuses=["EN", "XX"],
            watchlist="everything",
            limit=10_000,
        ),
        regions,
        TODAY,
    )
    assert (p.intent, p.group_by, p.watchlist) == ("unsupported", "none", "none")
    assert p.end == TODAY and (p.end - p.start).days <= 3660
    assert (p.region_ids, p.region_names) == ([1], ["Greater London"])
    assert p.groups == ["bird"] and p.iucn_statuses == ["EN"] and p.limit == 50
    assert any("Atlantis" in w for w in p.warnings)


def test_trend_and_top_species_force_sensible_grouping():
    assert validate_plan(plan(intent="trend"), {}, TODAY).group_by == "month"
    assert (
        validate_plan(plan(intent="top_species", group_by="region"), {}, TODAY).group_by
        == "species"
    )


# --- Engine ------------------------------------------------------------------------------


@pytest.fixture
def data(db, make_user, seeded):
    member, headers = make_user(Role.ANALYST)
    org = add_org_with_region(db, member, LONDON_SQUARE, role="admin")
    from app.services.organisations import OrganisationService

    OrganisationService(db).add_region(org, "North", square(-0.12, 51.65, 0.1))
    observer, _ = make_user()
    hawk, kestrel = taxon(db, "Aquila chrysaetos"), taxon(db, "Falco peregrinus")
    sparrow, squirrel = taxon(db, "Passer domesticus"), taxon(db, "Sciurus carolinensis")
    db.add(WatchlistEntry(organisation_id=org.id, taxon_id=squirrel.id, category="invasive"))
    db.commit()

    def add(t, days, where=(51.5072, -0.1276)):
        make_verified(db, observer, t, *where, NOW - timedelta(days=days))

    for d in (400, 420, 500, 600):  # raptors: 4 in the previous year...
        add(hawk, d)
    add(kestrel, 30)  # ...1 in the last year: a decline
    for d in (5, 10, 15):
        add(sparrow, d)
    add(sparrow, 20, where=(51.65, -0.12))  # in "North" (and the big London square)
    add(squirrel, 3)
    add(sparrow, 3, where=(48.85, 2.35))  # Paris: outside the organisation
    db.refresh(org)
    return org, headers, member


def run(db, org, **overrides):
    regions = {r.name: r.id for r in org.regions}
    return AnalystEngine(db, org.id).run(validate_plan(plan(**overrides), regions, TODAY))


def test_counts_by_species_are_scoped_to_the_organisation(db, data):
    org, *_ = data
    rows = run(db, org, group_by="species")["rows"]
    assert rows[0] == {  # Paris excluded
        "key": "Passer domesticus",
        "sightings": 4,
        "species": 1,
        "common_name": "House Sparrow",
    }
    assert sum(r["sightings"] for r in rows) == 6  # 4 sparrows, 1 kestrel, 1 squirrel this year


def test_raptor_decline_by_comparing_periods(db, data):
    org, *_ = data
    result = run(
        db, org, intent="compare_periods", group_by="none", families=["Accipitridae", "Falconidae"]
    )
    assert result["rows"] == [{"key": "all", "previous": 4, "current": 1, "change_pct": -75}]
    assert result["previous_period"][1] == (TODAY - timedelta(days=366)).isoformat()


def test_region_grouping_and_filters(db, data):
    org, *_ = data
    by_region = {
        r["key"]: r["sightings"] for r in run(db, org, group_by="region", groups=["bird"])["rows"]
    }
    assert by_region == {"Patch": 5, "North": 1}  # overlapping regions count in each
    north = run(db, org, regions=["north"])["rows"]
    assert north == [{"key": "all", "sightings": 1, "species": 1}]
    invasive = run(db, org, watchlist="invasive")["rows"]
    assert invasive[0]["sightings"] == 1
    endangered = run(db, org, iucn_statuses=["CR"])["rows"]
    assert endangered == []


def test_trend_by_month_is_chronological(db, data):
    org, *_ = data
    keys = [
        r["key"]
        for r in run(db, org, intent="trend", start=(TODAY - timedelta(days=700)).isoformat())[
            "rows"
        ]
    ]
    assert keys == sorted(keys) and len(keys) >= 2


# --- Service and API (fake Gemini) -------------------------------------------------------


class FakeLLM:
    provider, model = "fake", "fake-gemini"

    def __init__(self, plan_response=None, fail=False):
        self.plan_response = plan_response or plan(
            intent="compare_periods", families=["Accipitridae", "Falconidae"]
        )
        self.fail = fail
        self.calls = []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise LLMError("quota exceeded")
        if kwargs["schema_name"] == "query_plan":
            return self.plan_response
        if kwargs["schema_name"] == "analyst_answer":
            return {
                "answer": "Raptor records fell from 4 to 1.",
                "highlights": ["-75%"],
                "caveats": ["Effort varies."],
            }
        return {
            "title": "Monthly report",
            "summary": "Quiet month.",
            "sections": [{"heading": "Activity", "paragraphs": ["6 records."]}],
            "recommendations": ["Survey North."],
        }


def ask(client, org, headers, llm, monkeypatch, question="Which raptors are declining?"):
    from app.api.routes import insights

    monkeypatch.setattr(insights, "get_llm", lambda: llm)
    return client.post(
        f"/api/v1/orgs/{org.id}/analyst/ask", json={"question": question}, headers=headers
    )


def test_ask_plans_queries_and_answers_from_figures_only(client, db, data, monkeypatch):
    org, headers, _ = data
    llm = FakeLLM()
    res = ask(client, org, headers, llm, monkeypatch)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["answer"]["answer"] == "Raptor records fell from 4 to 1."
    assert body["result"]["rows"][0]["previous"] == 4
    assert body["plan"]["families"] == ["Accipitridae", "Falconidae"]

    plan_call, answer_call = llm.calls
    assert "Patch" in plan_call["system"] and TODAY.isoformat() in plan_call["system"]
    sent = json.loads(answer_call["prompt"])
    assert sent["results"]["rows"] == body["result"]["rows"]
    # Aggregates only: no coordinates or observer identities reach the model.
    assert "51.5" not in answer_call["prompt"] and "user" not in answer_call["prompt"]

    history = client.get(f"/api/v1/orgs/{org.id}/analyst/history", headers=headers).json()
    assert [h["question"] for h in history] == ["Which raptors are declining?"]


def test_unsupported_questions_get_an_answer_without_a_query(client, db, data, monkeypatch):
    org, headers, _ = data
    llm = FakeLLM(plan_response=plan(intent="unsupported"))
    body = ask(client, org, headers, llm, monkeypatch, "Who saw the tiger?").json()
    assert body["result"]["rows"] == [] and body["answer"]


def test_analyst_failure_is_recorded_and_reported(client, db, data, monkeypatch):
    org, headers, _ = data
    res = ask(client, org, headers, FakeLLM(fail=True), monkeypatch)
    assert res.status_code == 502 and res.json()["detail"]["code"] == "analyst_unavailable"
    assert db.scalar(select(AnalystQuery.error)) == "quota exceeded"


def test_daily_question_limit(client, db, data, monkeypatch):
    from app.services import analyst

    monkeypatch.setattr(analyst, "DAILY_QUESTION_LIMIT", 1)
    org, headers, _ = data
    assert ask(client, org, headers, FakeLLM(), monkeypatch).status_code == 200
    assert ask(client, org, headers, FakeLLM(), monkeypatch).status_code == 429


def test_members_only(client, data, make_user, monkeypatch):
    org, *_ = data
    _, stranger = make_user(Role.ANALYST)
    assert ask(client, org, stranger, FakeLLM(), monkeypatch).status_code == 404


def test_reports_are_written_from_aggregates_in_the_readers_language(client, db, data, monkeypatch):
    from app.api.routes import insights

    org, headers, member = data
    client.patch("/api/v1/auth/me", json={"locale": "hi"}, headers=headers)
    llm = FakeLLM()
    monkeypatch.setattr(insights, "get_llm", lambda: llm)
    res = client.post(f"/api/v1/orgs/{org.id}/reports", json={"period": "month"}, headers=headers)
    assert res.status_code == 201, res.text
    report = res.json()
    assert report["content"]["title"] == "Monthly report" and report["locale"] == "hi"
    assert "Hindi" in llm.calls[0]["system"]

    facts = db.scalar(select(Report.facts))
    assert facts["totals"]["observations"] == 5  # last 30 days: the 30-day-old kestrel is out
    assert facts["invasive_sightings"][0]["species"] == "Sciurus carolinensis"
    flat = json.dumps(facts)
    assert "photo_url" not in flat and "latitude" not in flat and "51.5" not in flat
    assert [
        r["id"] for r in client.get(f"/api/v1/orgs/{org.id}/reports", headers=headers).json()
    ] == [report["id"]]


def test_scheduled_reports_cli(db, data, monkeypatch):
    import app.adapters.llm

    monkeypatch.setattr(app.adapters.llm, "get_llm", lambda: FakeLLM())
    assert generate_reports(days=30, locale="en") == [("Test Trust", "Monthly report")]
