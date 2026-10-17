from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, enum_check


class TaxonGroup(StrEnum):
    ANIMAL = "animal"
    BIRD = "bird"
    PLANT = "plant"


class IucnStatus(StrEnum):
    EXTINCT = "EX"
    EXTINCT_IN_WILD = "EW"
    CRITICALLY_ENDANGERED = "CR"
    ENDANGERED = "EN"
    VULNERABLE = "VU"
    NEAR_THREATENED = "NT"
    LEAST_CONCERN = "LC"
    DATA_DEFICIENT = "DD"
    NOT_EVALUATED = "NE"


class TaxonSource(StrEnum):
    SEED = "seed"  # curated reference list
    GBIF = "gbif"  # added automatically after matching the GBIF backbone


class StatusSource(StrEnum):
    SEED = "seed"  # curated sample list
    GBIF_IUCN = "gbif_iucn"  # IUCN Red List category via GBIF
    MANUAL = "manual"  # set by an admin


class Taxon(Timestamps, Base):
    __tablename__ = "taxa"
    __table_args__ = (
        CheckConstraint(enum_check("taxon_group", TaxonGroup), name="taxon_group"),
        CheckConstraint(enum_check("iucn_status", IucnStatus), name="iucn_status"),
        CheckConstraint(enum_check("source", TaxonSource), name="source"),
        CheckConstraint(enum_check("status_source", StatusSource), name="status_source"),
        CheckConstraint("sensitivity IN ('open', 'obscured', 'hidden')", name="sensitivity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scientific_name: Mapped[str] = mapped_column(String(200), unique=True)
    # Localised vernacular names, e.g. {"en": "House Sparrow", "hi": "गौरैया"}.
    common_names: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    taxon_group: Mapped[str] = mapped_column(String(10), index=True)
    kingdom: Mapped[str] = mapped_column(String(50))
    class_name: Mapped[str | None] = mapped_column(String(80))
    family: Mapped[str | None] = mapped_column(String(80))
    iucn_status: Mapped[str] = mapped_column(String(2), default=IucnStatus.NOT_EVALUATED.value)
    gbif_key: Mapped[int | None] = mapped_column(Integer, unique=True)
    # Where iucn_status came from; NULL means never checked (treated as sensitive).
    status_source: Mapped[str | None] = mapped_column(String(10))
    status_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Admin override of location sensitivity (e.g. "hidden" for heavily poached species);
    # NULL derives it from the conservation status.
    sensitivity: Mapped[str | None] = mapped_column(String(10))
    # Range size: countries with GBIF records, the endemism signal for rarity scores.
    range_country_count: Mapped[int | None] = mapped_column(Integer)
    range_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(
        String(10), default=TaxonSource.SEED.value, server_default=TaxonSource.SEED.value
    )

    def common_name(self, locale: str) -> str | None:
        return self.common_names.get(locale) or self.common_names.get("en")
