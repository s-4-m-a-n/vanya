import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import Role, TaxonGroup


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    locale: str = Field(default="en", min_length=2, max_length=10)

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    locale: str
    reputation: int
    location_consent_at: datetime | None
    review_taxon_groups: list[TaxonGroup]
    review_latitude: float | None
    review_longitude: float | None
    review_radius_km: float | None
    leaderboard_visible: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class RoleUpdate(BaseModel):
    role: Role


class ReviewArea(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(ge=25, le=20_000)


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    locale: str | None = Field(default=None, min_length=2, max_length=10)
    # True records consent now; False withdraws it and blocks further capture.
    location_consent: bool | None = None
    # Verifiers only: which groups and area their review queue shows by default.
    review_taxon_groups: list[TaxonGroup] | None = None
    # Send null to clear the review area.
    review_area: ReviewArea | None = None
    leaderboard_visible: bool | None = None
