"""Alerts, the Gemini analyst and reports for organisation members (PRD F6, F7)."""

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.adapters.events import get_event_bus
from app.adapters.llm import get_llm
from app.adapters.storage import BlobStorage, get_storage
from app.api.deps import CurrentUser, DbSession, service_error
from app.config import get_settings
from app.models import Alert, AnalystQuery, Report
from app.services.alerts import AlertService
from app.services.analyst import AnalystService
from app.services.observations import ObservationError
from app.services.organisations import OrganisationService

router = APIRouter(prefix="/orgs", tags=["insights"])


def get_alert_service(db: DbSession) -> AlertService:
    return AlertService(db, get_event_bus(), get_settings())


def get_analyst_service(
    db: DbSession, storage: Annotated[BlobStorage, Depends(get_storage)]
) -> AnalystService:
    # get_llm is looked up at call time so tests (and provider switches) take effect.
    return AnalystService(db, lambda: get_llm(), storage)


Alerts = Annotated[AlertService, Depends(get_alert_service)]
Analyst = Annotated[AnalystService, Depends(get_analyst_service)]


def _org(db, org_id: uuid.UUID, user, admin: bool = False):  # noqa: ANN001, ANN202
    try:
        return OrganisationService(db).get_for(org_id, user, admin=admin)
    except ObservationError as exc:
        raise service_error(exc) from exc


def _call(fn, *args):  # noqa: ANN001, ANN002, ANN202
    try:
        return fn(*args)
    except ObservationError as exc:
        raise service_error(exc) from exc


# --- Alerts ----------------------------------------------------------------------------


class AlertOut(BaseModel):
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    observed_at: datetime
    verified_at: datetime | None
    created_at: datetime
    acknowledged_at: datetime | None

    @classmethod
    def build(cls, alert: Alert) -> "AlertOut":
        return cls.model_validate(alert, from_attributes=True)


class AlertInbox(BaseModel):
    items: list[AlertOut]
    unread: int


@router.get("/{org_id}/alerts")
def alerts(
    org_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    service: Alerts,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AlertInbox:
    org = _org(db, org_id, user)
    items, unread = service.inbox(org.id, unread_only, limit)
    return AlertInbox(items=[AlertOut.build(a) for a in items], unread=unread)


@router.post("/{org_id}/alerts/{alert_id}/acknowledge")
def acknowledge(
    org_id: uuid.UUID, alert_id: uuid.UUID, user: CurrentUser, db: DbSession, service: Alerts
) -> AlertOut:
    org = _org(db, org_id, user)
    return AlertOut.build(_call(service.acknowledge, org.id, alert_id, user))


class AlertSettingsIn(BaseModel):
    notify_invasive: bool | None = None
    notify_new_record: bool | None = None
    notify_priority: bool | None = None
    webhook_url: str | None = Field(default=None, max_length=500)


class AlertSettingsOut(BaseModel):
    notify_invasive: bool
    notify_new_record: bool
    notify_priority: bool
    webhook_url: str | None
    has_webhook_secret: bool


def _settings_out(prefs) -> AlertSettingsOut:  # noqa: ANN001
    return AlertSettingsOut(
        notify_invasive=prefs.notify_invasive,
        notify_new_record=prefs.notify_new_record,
        notify_priority=prefs.notify_priority,
        webhook_url=prefs.webhook_url,
        has_webhook_secret=bool(prefs.webhook_secret),
    )


@router.get("/{org_id}/alert-settings")
def alert_settings(
    org_id: uuid.UUID, user: CurrentUser, db: DbSession, service: Alerts
) -> AlertSettingsOut:
    return _settings_out(service.settings_for(_org(db, org_id, user).id))


@router.put("/{org_id}/alert-settings")
def update_alert_settings(
    org_id: uuid.UUID, body: AlertSettingsIn, user: CurrentUser, db: DbSession, service: Alerts
) -> AlertSettingsOut:
    org = _org(db, org_id, user, admin=True)
    changes = body.model_dump(exclude_unset=True)
    return _settings_out(_call(service.update_settings, org, changes))


@router.post("/{org_id}/alert-settings/rotate-secret")
def rotate_secret(
    org_id: uuid.UUID, user: CurrentUser, db: DbSession, service: Alerts
) -> dict[str, str]:
    """Returns the new signing secret once; it's never shown again."""
    org = _org(db, org_id, user, admin=True)
    return {"webhook_secret": _call(service.rotate_secret, org)}


@router.post("/{org_id}/alert-settings/test-webhook")
def test_webhook(
    org_id: uuid.UUID, user: CurrentUser, db: DbSession, service: Alerts
) -> dict[str, Any]:
    result = _call(service.test_webhook, _org(db, org_id, user, admin=True))
    return {"ok": result.ok, "http_status": result.http_status, "error": result.error}


# --- Analyst ---------------------------------------------------------------------------


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class AnswerOut(BaseModel):
    id: uuid.UUID
    question: str
    plan: dict[str, Any] | None
    result: dict[str, Any] | None
    answer: dict[str, Any] | None
    model: str | None
    latency_ms: int | None
    created_at: datetime

    @classmethod
    def build(cls, record: AnalystQuery) -> "AnswerOut":
        return cls.model_validate(record, from_attributes=True)


@router.post("/{org_id}/analyst/ask")
def ask(
    org_id: uuid.UUID, body: Question, user: CurrentUser, db: DbSession, analyst: Analyst
) -> AnswerOut:
    org = _org(db, org_id, user)
    return AnswerOut.build(_call(analyst.ask, org, user, body.question))


@router.get("/{org_id}/analyst/history")
def history(
    org_id: uuid.UUID, user: CurrentUser, db: DbSession, analyst: Analyst
) -> list[AnswerOut]:
    org = _org(db, org_id, user)
    return [AnswerOut.build(r) for r in analyst.history(org, user)]


# --- Reports ---------------------------------------------------------------------------


class ReportRequest(BaseModel):
    period: Literal["week", "month", "quarter"] = "month"


class ReportOut(BaseModel):
    id: uuid.UUID
    period_start: date
    period_end: date
    locale: str
    content: dict[str, Any]
    model: str | None
    created_at: datetime

    @classmethod
    def build(cls, report: Report) -> "ReportOut":
        return cls.model_validate(report, from_attributes=True)


PERIOD_DAYS = {"week": 7, "month": 30, "quarter": 91}


@router.post("/{org_id}/reports", status_code=status.HTTP_201_CREATED)
def create_report(
    org_id: uuid.UUID, body: ReportRequest, user: CurrentUser, db: DbSession, analyst: Analyst
) -> ReportOut:
    org = _org(db, org_id, user)
    end = date.today()
    start = end - timedelta(days=PERIOD_DAYS[body.period] - 1)
    return ReportOut.build(_call(analyst.report, org, start, end, user.locale, user))


@router.get("/{org_id}/reports")
def reports(org_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[ReportOut]:
    org = _org(db, org_id, user)
    rows = db.scalars(
        select(Report)
        .where(Report.organisation_id == org.id)
        .order_by(Report.created_at.desc())
        .limit(50)
    )
    return [ReportOut.build(r) for r in rows]
