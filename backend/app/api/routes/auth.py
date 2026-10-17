from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import Role, User
from app.schemas.auth import (
    LoginRequest,
    ProfileUpdate,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        user=UserOut.model_validate(user),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: DbSession) -> TokenResponse:
    if db.scalar(select(User.id).where(User.email == body.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered")
    user = User(
        email=body.email,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        role=Role.OBSERVER.value,
        locale=body.locale,
    )
    db.add(user)
    db.commit()
    return _token_response(user)


@router.post("/login")
def login(body: LoginRequest, db: DbSession) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == body.email, User.deleted_at.is_(None)))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return _token_response(user)


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me")
def update_me(body: ProfileUpdate, user: CurrentUser, db: DbSession) -> UserOut:
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.locale is not None:
        user.locale = body.locale
    if body.leaderboard_visible is not None:
        user.leaderboard_visible = body.leaderboard_visible
    if body.location_consent is True and user.location_consent_at is None:
        user.location_consent_at = datetime.now(UTC)
    elif body.location_consent is False:
        user.location_consent_at = None

    wants_review_prefs = "review_taxon_groups" in body.model_fields_set or (
        "review_area" in body.model_fields_set
    )
    if wants_review_prefs and user.role not in (Role.VERIFIER.value, Role.ADMIN.value):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only verifiers have a review queue")
    if body.review_taxon_groups is not None:
        user.review_taxon_groups = sorted({g.value for g in body.review_taxon_groups})
    if "review_area" in body.model_fields_set:
        area = body.review_area
        user.review_latitude = area.latitude if area else None
        user.review_longitude = area.longitude if area else None
        user.review_radius_km = area.radius_km if area else None
    db.commit()
    return UserOut.model_validate(user)
