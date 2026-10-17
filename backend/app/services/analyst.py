"""Gemini analyst (PRD F7): natural-language questions and periodic reports.

The model never writes SQL. It turns a question into a structured plan from a fixed
menu; we validate the plan and run our own parameterised query, scoped to the
organisation's regions and verified records; then the model writes the answer from those
figures only. The model only ever sees aggregate counts and species names: no
coordinates, no observer identities.
"""

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, distinct, func, literal, select
from sqlalchemy.orm import Session

from app.adapters.llm import LLMClient, LLMError
from app.adapters.storage import BlobStorage
from app.models import (
    Alert,
    AnalystQuery,
    IucnStatus,
    Observation,
    ObservationStatus,
    Organisation,
    RegionOfInterest,
    Report,
    Taxon,
    TaxonGroup,
    User,
    WatchlistEntry,
)
from app.services.dashboard import DashboardService, Scope
from app.services.observations import ObservationError

LANGUAGES = {"en": "English", "hi": "Hindi", "es": "Spanish"}
DAILY_QUESTION_LIMIT = 50
MAX_ROWS = 50
MAX_RANGE = timedelta(days=3660)
INTENTS = ["counts", "trend", "compare_periods", "top_species", "unsupported"]
GROUP_BYS = ["none", "region", "species", "group", "month", "year"]


def _strings(description: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": description}


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "group_by": {"type": "string", "enum": GROUP_BYS},
        "start": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
        "end": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
        "scientific_names": _strings("Binomial species names"),
        "families": _strings("Taxonomic families, e.g. Accipitridae"),
        "classes": _strings("Taxonomic classes, e.g. Aves, Mammalia"),
        "groups": {
            "type": "array",
            "items": {"type": "string", "enum": [g.value for g in TaxonGroup]},
        },
        "iucn_statuses": {
            "type": "array",
            "items": {"type": "string", "enum": [s.value for s in IucnStatus]},
        },
        "watchlist": {"type": "string", "enum": ["none", "invasive", "priority"]},
        "regions": _strings("Names of the organisation's regions; empty for all"),
        "limit": {"type": "integer"},
        "interpretation": {"type": "string"},
    },
    "required": [
        "intent",
        "group_by",
        "start",
        "end",
        "scientific_names",
        "families",
        "classes",
        "groups",
        "iucn_statuses",
        "watchlist",
        "regions",
        "limit",
        "interpretation",
    ],
    "additionalProperties": False,
}

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "highlights", "caveats"],
    "additionalProperties": False,
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["heading", "paragraphs"],
                "additionalProperties": False,
            },
        },
        "recommendations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "sections", "recommendations"],
    "additionalProperties": False,
}

GROUNDING = (
    "Use only the figures provided; never invent numbers, species or places. Counts of "
    "verified sightings reflect how much people recorded as well as the wildlife itself, "
    "so describe changes as changes in records, not proven population trends."
)


@dataclass
class Plan:
    intent: str
    group_by: str
    start: date
    end: date  # inclusive
    scientific_names: list[str] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    iucn_statuses: list[str] = field(default_factory=list)
    watchlist: str = "none"
    region_ids: list[int] = field(default_factory=list)
    region_names: list[str] = field(default_factory=list)
    limit: int = 10
    interpretation: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["start"], data["end"] = self.start.isoformat(), self.end.isoformat()
        return data


def _clean(values: Any, limit: int = 20) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(v).strip()[:100] for v in values if str(v).strip()][:limit]


def validate_plan(raw: dict[str, Any], regions: dict[str, int], today: date) -> Plan:
    """Turn the model's plan into one we're willing to run: known values only,
    bounded dates and sizes, region names mapped to this organisation's regions."""
    warnings = []

    def parse(value: Any, default: date) -> date:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return default

    end = min(parse(raw.get("end"), today), today)
    start = parse(raw.get("start"), end - timedelta(days=365))
    if start > end:
        start, end = end, start
    if end - start > MAX_RANGE:
        start = end - MAX_RANGE
        warnings.append("Time range shortened to ten years.")

    region_ids, region_names = [], []
    by_lower = {name.lower(): (name, rid) for name, rid in regions.items()}
    for name in _clean(raw.get("regions")):
        match = by_lower.get(name.lower())
        if match:
            region_names.append(match[0])
            region_ids.append(match[1])
        else:
            warnings.append(f"No region called '{name}'; using all regions.")

    intent = raw.get("intent") if raw.get("intent") in INTENTS else "unsupported"
    group_by = raw.get("group_by") if raw.get("group_by") in GROUP_BYS else "none"
    if intent == "trend" and group_by not in ("month", "year"):
        group_by = "month"
    if intent == "top_species":
        group_by = "species"
    try:
        limit = min(max(int(raw.get("limit") or 10), 1), MAX_ROWS)
    except (TypeError, ValueError):
        limit = 10
    groups = [g for g in _clean(raw.get("groups")) if g in {t.value for t in TaxonGroup}]
    statuses = [s for s in _clean(raw.get("iucn_statuses")) if s in {i.value for i in IucnStatus}]
    watchlist = raw.get("watchlist") if raw.get("watchlist") in ("invasive", "priority") else "none"
    return Plan(
        intent=intent,
        group_by=group_by,
        start=start,
        end=end,
        scientific_names=_clean(raw.get("scientific_names")),
        families=_clean(raw.get("families")),
        classes=_clean(raw.get("classes")),
        groups=groups,
        iucn_statuses=statuses,
        watchlist=watchlist,
        region_ids=region_ids,
        region_names=region_names,
        limit=limit,
        interpretation=str(raw.get("interpretation") or "")[:500],
        warnings=warnings,
    )


class AnalystEngine:
    """Runs a validated plan. Every query: verified records, this organisation's regions."""

    def __init__(self, db: Session, org_id: uuid.UUID, locale: str = "en") -> None:
        self.db = db
        self.org_id = org_id
        self.locale = locale

    def run(self, plan: Plan) -> dict[str, Any]:
        result = self._run(plan)
        if plan.group_by == "species" and result["rows"]:
            # Common names in the reader's language make answers readable.
            names = {
                t.scientific_name: t.common_name(self.locale)
                for t in self.db.scalars(
                    select(Taxon).where(
                        Taxon.scientific_name.in_([r["key"] for r in result["rows"]])
                    )
                )
            }
            for row in result["rows"]:
                row["common_name"] = names.get(row["key"])
        return result

    def _run(self, plan: Plan) -> dict[str, Any]:
        start = datetime.combine(plan.start, datetime.min.time(), UTC)
        end = datetime.combine(plan.end + timedelta(days=1), datetime.min.time(), UTC)
        if plan.intent == "compare_periods":
            span = end - start
            current = self._grouped(plan, start, end)
            previous = self._grouped(plan, start - span, start)
            keys = sorted(
                current.keys() | previous.keys(),
                key=lambda k: abs(current.get(k, (0, 0))[0] - previous.get(k, (0, 0))[0]),
                reverse=True,
            )[: plan.limit]
            rows = []
            for key in keys:
                now, before = current.get(key, (0, 0))[0], previous.get(key, (0, 0))[0]
                rows.append(
                    {
                        "key": key,
                        "previous": before,
                        "current": now,
                        "change_pct": round((now - before) / before * 100) if before else None,
                    }
                )
            return {
                "intent": plan.intent,
                "group_by": plan.group_by,
                "period": [plan.start.isoformat(), plan.end.isoformat()],
                "previous_period": [
                    (plan.start - (plan.end - plan.start) - timedelta(days=1)).isoformat(),
                    (plan.start - timedelta(days=1)).isoformat(),
                ],
                "columns": ["key", "previous", "current", "change_pct"],
                "rows": rows,
            }

        grouped = self._grouped(plan, start, end)
        items = list(grouped.items())
        if plan.group_by in ("month", "year"):
            items.sort(key=lambda kv: kv[0])
        else:
            items.sort(key=lambda kv: kv[1][0], reverse=True)
        return {
            "intent": plan.intent,
            "group_by": plan.group_by,
            "period": [plan.start.isoformat(), plan.end.isoformat()],
            "columns": ["key", "sightings", "species"],
            "rows": [
                {"key": key, "sightings": n, "species": s} for key, (n, s) in items[: plan.limit]
            ],
        }

    def _grouped(self, plan: Plan, start: datetime, end: datetime) -> dict[str, tuple[int, int]]:
        key = {
            "none": literal("all"),
            "region": RegionOfInterest.name,
            "species": Taxon.scientific_name,
            "group": Taxon.taxon_group,
            "month": func.to_char(func.date_trunc("month", Observation.observed_at), "YYYY-MM"),
            "year": func.to_char(func.date_trunc("year", Observation.observed_at), "YYYY"),
        }[plan.group_by]
        stmt = (
            select(key, func.count(Observation.id), func.count(distinct(Observation.taxon_id)))
            .select_from(Observation)
            .join(Taxon, Observation.taxon_id == Taxon.id)
            .where(
                Observation.status == ObservationStatus.VERIFIED.value,
                Observation.observed_at >= start,
                Observation.observed_at < end,
            )
        )
        in_region = [
            RegionOfInterest.organisation_id == self.org_id,
            func.ST_Intersects(RegionOfInterest.area, Observation.generalised_location),
        ]
        if plan.region_ids:
            in_region.append(RegionOfInterest.id.in_(plan.region_ids))
        if plan.group_by == "region":
            # A record in two overlapping regions counts once in each.
            stmt = stmt.join(RegionOfInterest, and_(*in_region))
        else:
            stmt = stmt.where(select(RegionOfInterest.id).where(*in_region).exists())

        if plan.scientific_names:
            stmt = stmt.where(
                func.lower(Taxon.scientific_name).in_([n.lower() for n in plan.scientific_names])
            )
        if plan.families:
            stmt = stmt.where(func.lower(Taxon.family).in_([f.lower() for f in plan.families]))
        if plan.classes:
            stmt = stmt.where(func.lower(Taxon.class_name).in_([c.lower() for c in plan.classes]))
        if plan.groups:
            stmt = stmt.where(Taxon.taxon_group.in_(plan.groups))
        if plan.iucn_statuses:
            stmt = stmt.where(Taxon.iucn_status.in_(plan.iucn_statuses))
        if plan.watchlist != "none":
            stmt = stmt.where(
                Observation.taxon_id.in_(
                    select(WatchlistEntry.taxon_id).where(
                        WatchlistEntry.organisation_id == self.org_id,
                        WatchlistEntry.category == plan.watchlist,
                    )
                )
            )
        rows = self.db.execute(stmt.group_by(key)).all()
        return {str(k): (n, s) for k, n, s in rows}


class AnalystService:
    def __init__(
        self, db: Session, llm_factory: Callable[[], LLMClient], storage: BlobStorage
    ) -> None:
        self.db = db
        self.llm_factory = llm_factory
        self.storage = storage

    def ask(self, org: Organisation, user: User, question: str) -> AnalystQuery:
        if self._asked_today(user) >= DAILY_QUESTION_LIMIT:
            raise ObservationError("question_limit", "Daily question limit reached", 429)
        locale = user.locale if user.locale in LANGUAGES else "en"
        record = AnalystQuery(
            organisation_id=org.id, user_id=user.id, question=question, locale=locale
        )
        started = time.perf_counter()
        try:
            llm = self.llm_factory()
            record.model = llm.model
            regions = {r.name: r.id for r in org.regions}
            raw = llm.generate_json(
                system=self._plan_system(regions, date.today()),
                prompt=f"Question: {question}",
                schema=PLAN_SCHEMA,
                schema_name="query_plan",
                temperature=0.0,
            )
            plan = validate_plan(raw, regions, date.today())
            record.plan = plan.as_json()
            if plan.intent == "unsupported":
                record.result = {"rows": [], "columns": []}
            else:
                record.result = AnalystEngine(self.db, org.id, locale).run(plan)
            record.answer = llm.generate_json(
                system=(
                    "You are a conservation data analyst writing for park managers and "
                    f"researchers. {GROUNDING} Write in {LANGUAGES[locale]}. Be concise: "
                    "a direct answer first, then up to five highlights. If the question "
                    "can't be answered from this data, say so and suggest what could be asked."
                ),
                prompt=_json(
                    {
                        "question": question,
                        "interpretation": plan.interpretation,
                        "notes": plan.warnings,
                        "results": record.result,
                    }
                ),
                schema=ANSWER_SCHEMA,
                schema_name="analyst_answer",
                temperature=0.2,
            )
        except LLMError as exc:
            record.error = str(exc)[:300]
        record.latency_ms = int((time.perf_counter() - started) * 1000)
        self.db.add(record)
        self.db.commit()
        if record.error:
            raise ObservationError("analyst_unavailable", "The analyst is unavailable", 502)
        return record

    def history(self, org: Organisation, user: User, limit: int = 20) -> list[AnalystQuery]:
        return list(
            self.db.scalars(
                select(AnalystQuery)
                .where(AnalystQuery.organisation_id == org.id, AnalystQuery.user_id == user.id)
                .order_by(AnalystQuery.created_at.desc())
                .limit(limit)
            )
        )

    def report(
        self, org: Organisation, start: date, end: date, locale: str, user: User | None
    ) -> Report:
        locale = locale if locale in LANGUAGES else "en"
        facts = self.report_facts(org, start, end, locale)
        try:
            llm = self.llm_factory()
            content = llm.generate_json(
                system=(
                    "You write short periodic reports on wildlife records for a conservation "
                    f"organisation. {GROUNDING} Write in {LANGUAGES[locale]}. Structure: a "
                    "summary paragraph, then sections on overall activity, rare and "
                    "threatened species, invasive species, new records and notable changes, "
                    "then practical recommendations. Skip sections with nothing to report."
                ),
                prompt=_json({"organisation": org.name, **facts}),
                schema=REPORT_SCHEMA,
                schema_name="report",
                temperature=0.3,
            )
        except LLMError as exc:
            raise ObservationError(
                "analyst_unavailable", "The analyst is unavailable", 502
            ) from exc
        report = Report(
            organisation_id=org.id,
            period_start=start,
            period_end=end,
            locale=locale,
            content=content,
            facts=facts,
            model=llm.model,
            created_by=user.id if user else None,
        )
        self.db.add(report)
        self.db.commit()
        return report

    def report_facts(self, org: Organisation, start: date, end: date, locale: str) -> dict:
        """Aggregates only: no coordinates, photos or observer identities."""
        scope = Scope(
            org_id=org.id,
            start=datetime.combine(start, datetime.min.time(), UTC),
            end=datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC),
        )
        board = DashboardService(self.db, self.storage, locale)

        def species(record: dict) -> dict:
            return {
                "species": record["taxon"]["scientific_name"],
                "common_name": record["taxon"]["common_name"],
                "iucn_status": record["taxon"]["iucn_status"],
                "observed_on": record["observed_at"][:10],
                "rarity_tier": record.get("rarity_tier"),
            }

        alerts = dict(
            self.db.execute(
                select(Alert.kind, func.count())
                .where(
                    Alert.organisation_id == org.id,
                    Alert.created_at >= scope.start,
                    Alert.created_at < scope.end,
                )
                .group_by(Alert.kind)
            ).all()
        )
        summary = board.summary(scope)
        return {
            "period": [start.isoformat(), end.isoformat()],
            "regions": [r.name for r in org.regions],
            "totals": {k: v for k, v in summary.items() if k != "top_species"},
            "top_species": [
                {
                    "species": t["taxon"]["scientific_name"],
                    "common_name": t["taxon"]["common_name"],
                    "count": t["count"],
                }
                for t in summary["top_species"]
            ],
            "changes_vs_previous_period": board.changes(scope),
            "rare_and_threatened": [species(r) for r in board.rare(scope, limit=20)],
            "invasive_sightings": [species(r) for r in board.invasive(scope, limit=20)],
            "new_records": [
                {
                    "species": r["taxon"]["scientific_name"],
                    "common_name": r["taxon"]["common_name"],
                    "first_seen": r["first_seen"][:10],
                }
                for r in board.new_records(scope, limit=20)
            ],
            "alerts": alerts,
        }

    def _asked_today(self, user: User) -> int:
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self.db.scalar(
                select(func.count()).where(
                    AnalystQuery.user_id == user.id, AnalystQuery.created_at >= midnight
                )
            )
            or 0
        )

    @staticmethod
    def _plan_system(regions: dict[str, int], today: date) -> str:
        names = ", ".join(sorted(regions)) or "(none)"
        return (
            "You turn questions about verified wildlife sightings into a query plan for one "
            "conservation organisation. The data: verified sightings of animals, birds and "
            "plants inside the organisation's regions, with species (scientific name, "
            "family, class), group (bird, animal, plant), IUCN status, region and date.\n"
            f"Today is {today.isoformat()}. The organisation's regions: {names}.\n"
            "Intents: counts (totals, optionally grouped), trend (by month or year), "
            "compare_periods (the period against the previous period of equal length, for "
            "questions about increases or declines), top_species, or unsupported (anything "
            "this data can't answer, e.g. weather, causes, individual people or exact "
            "locations).\n"
            "Map common words to taxonomy where needed: e.g. raptors or birds of prey -> "
            "families Accipitridae, Falconidae, Pandionidae; owls -> Strigidae, Tytonidae; "
            "frogs and toads -> class Amphibia. Use empty lists for filters you don't need. "
            "Resolve relative dates against today ('two years' -> the last 730 days). "
            "Write `interpretation` as one sentence, in the same language as the question, "
            "describing exactly what will be counted."
        )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
