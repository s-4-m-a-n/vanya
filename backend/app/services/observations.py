"""Capture flow (PRD F1): photo → storage → identification → taxon resolution → record."""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.events import EventBus
from app.adapters.species_id import IdContext, SpeciesIdentifier, SpeciesIdError
from app.adapters.storage import BlobStorage
from app.config import Settings
from app.models import (
    Geoprivacy,
    Habitat,
    IdCandidate,
    LocationSource,
    Observation,
    ObservationStatus,
    Taxon,
    User,
)
from app.services.geoprivacy import apply_geoprivacy
from app.services.integrity import capture_flags
from app.services.photos import PhotoExif, process_photo
from app.services.taxonomy import TaxonResolver

logger = logging.getLogger(__name__)

# Tolerate small clock drift between phones and the server.
MAX_CLOCK_SKEW = timedelta(minutes=10)
EDITABLE_STATUSES = {ObservationStatus.NEEDS_ID.value, ObservationStatus.SUBMITTED.value}


class ObservationError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class NewObservation:
    client_uuid: uuid.UUID
    latitude: float | None = None
    longitude: float | None = None
    accuracy_m: float | None = None
    observed_at: datetime | None = None
    habitat: Habitat | None = None
    notes: str | None = None
    taxon_id: int | None = None
    # The device's offset from UTC; used to read EXIF times written without a timezone.
    utc_offset_minutes: int | None = None
    # The observer asks for their exact location never to be shown publicly.
    obscure_location: bool = False


class ObservationService:
    def __init__(
        self,
        db: Session,
        storage: BlobStorage,
        identifier: SpeciesIdentifier,
        resolver: TaxonResolver,
        events: EventBus,
        settings: Settings,
    ) -> None:
        self.db = db
        self.storage = storage
        self.identifier = identifier
        self.resolver = resolver
        self.events = events
        self.settings = settings

    def find_by_client_uuid(self, user: User, client_uuid: uuid.UUID) -> Observation | None:
        return self.db.scalar(
            select(Observation).where(
                Observation.user_id == user.id, Observation.client_uuid == client_uuid
            )
        )

    def create(self, user: User, photo: bytes, data: NewObservation) -> tuple[Observation, bool]:
        """Return (observation, created). Replays of the same client_uuid return the original."""
        if user.location_consent_at is None:
            raise ObservationError(
                "location_consent_required", "Location consent is required", status_code=403
            )
        existing = self.find_by_client_uuid(user, data.client_uuid)
        if existing is not None:
            return existing, False

        taxon = self._chosen_taxon(data.taxon_id)
        processed = process_photo(photo)
        latitude, longitude, source = _location(data, processed.exif)
        exif_time = _as_aware(processed.exif.taken_at, data.utc_offset_minutes)
        device_time = _as_aware(data.observed_at, data.utc_offset_minutes)
        observed_at = _observed_at(device_time, exif_time)

        observation = Observation(
            id=uuid.uuid4(),
            user_id=user.id,
            client_uuid=data.client_uuid,
            taxon_id=taxon.id if taxon else None,
            observer_taxon_id=taxon.id if taxon else None,
            status=(ObservationStatus.SUBMITTED if taxon else ObservationStatus.NEEDS_ID).value,
            latitude=latitude,
            longitude=longitude,
            accuracy_m=data.accuracy_m,
            location_source=source.value,
            observed_at=observed_at,
            habitat=data.habitat.value if data.habitat else None,
            notes=data.notes,
            # Keyed by client_uuid so concurrent retries of one capture share a single blob.
            photo_key=f"observations/{user.id}/{data.client_uuid}.jpg",
            photo_sha256=processed.sha256,
            photo_width=processed.width,
            photo_height=processed.height,
            exif=processed.exif.as_json(),
            photo_dhash=processed.dhash,
            observer_geoprivacy=(
                Geoprivacy.OBSCURED if data.obscure_location else Geoprivacy.OPEN
            ).value,
        )
        apply_geoprivacy(observation, taxon)
        self._identify(observation, processed.id_jpeg, user.locale)
        observation.integrity_flags = [
            flag.as_json()
            for flag in capture_flags(
                processed.exif,
                exif_time,
                data.latitude,
                data.longitude,
                data.accuracy_m,
                device_time,
                # Unset (not yet defaulted) when identification failed.
                observation.quality_flags or [],
                self.settings.location_mismatch_km,
                self.settings.time_mismatch_hours,
            )
        ]

        self.storage.put(observation.photo_key, processed.jpeg, "image/jpeg")
        self.db.add(observation)
        try:
            self.db.commit()
        except IntegrityError:
            # A concurrent retry of the same capture won the race; keep its record.
            self.db.rollback()
            existing = self.find_by_client_uuid(user, data.client_uuid)
            if existing is None:
                self.storage.delete(observation.photo_key)
                raise
            return existing, False

        if taxon:
            self._publish_submitted(observation)
        return observation, True

    def choose_species(self, observation: Observation, taxon_id: int) -> Observation:
        if observation.status not in EDITABLE_STATUSES:
            raise ObservationError(
                "not_editable", "This observation is already being verified", status_code=409
            )
        taxon = self._chosen_taxon(taxon_id)
        observation.taxon = taxon
        observation.observer_taxon_id = taxon.id
        apply_geoprivacy(observation, taxon)
        observation.status = ObservationStatus.SUBMITTED.value
        self.db.commit()
        self._publish_submitted(observation)
        return observation

    def delete(self, observation: Observation) -> None:
        observation_id = str(observation.id)
        self.db.delete(observation)
        self.db.commit()
        self.storage.delete(observation.photo_key)
        self.events.publish("observation.deleted", {"observation_id": observation_id})

    def _chosen_taxon(self, taxon_id: int | None) -> Taxon | None:
        if taxon_id is None:
            return None
        taxon = self.db.get(Taxon, taxon_id)
        if taxon is None:
            raise ObservationError("unknown_taxon", f"Unknown taxon {taxon_id}")
        return taxon

    def _identify(self, observation: Observation, image: bytes, locale: str) -> None:
        context = IdContext(
            latitude=observation.latitude,
            longitude=observation.longitude,
            observed_at=observation.observed_at,
            locale=locale,
        )
        started = time.perf_counter()
        try:
            result = self.identifier.identify(
                image, "image/jpeg", context, self.settings.species_id_top_k
            )
        except SpeciesIdError as exc:
            # Capture must still succeed; the observer can pick the species manually.
            logger.warning("Species identification failed: %s", exc)
            observation.id_error = str(exc)[:300]
            return
        finally:
            observation.id_latency_ms = int((time.perf_counter() - started) * 1000)

        observation.id_provider = result.provider
        observation.id_model = result.model
        observation.id_is_organism = result.is_organism
        observation.id_explanation = result.explanation
        observation.quality_flags = result.photo_flags
        if observation.habitat is None and result.habitat:
            observation.habitat = result.habitat
        for rank, candidate in enumerate(result.candidates, start=1):
            resolution = self.resolver.resolve(
                candidate.scientific_name,
                candidate.common_name,
                locale,
                candidate.taxon_group,
                english_name=candidate.english_name,
            )
            observation.candidates.append(
                IdCandidate(
                    rank=rank,
                    scientific_name=candidate.scientific_name,
                    common_name=candidate.common_name,
                    confidence=candidate.confidence,
                    taxon_id=resolution.taxon.id if resolution.taxon else None,
                    in_scope=resolution.in_scope,
                )
            )

    def _publish_submitted(self, observation: Observation) -> None:
        self.events.publish(
            "observation.submitted",
            {
                "observation_id": str(observation.id),
                "user_id": str(observation.user_id),
                "taxon_id": observation.taxon_id,
            },
        )
        # With synchronous delivery (tests) the verification handler has already run in
        # another session, so pick up its result. Otherwise it runs in the background and
        # the record reads "submitted" until it finishes; clients poll for the outcome.
        self.db.refresh(observation)


def _location(data: NewObservation, exif: PhotoExif) -> tuple[float, float, LocationSource]:
    if (data.latitude is None) != (data.longitude is None):
        raise ObservationError("invalid_location", "Send both latitude and longitude")
    if data.latitude is not None and data.longitude is not None:
        return data.latitude, data.longitude, LocationSource.DEVICE
    if exif.latitude is not None and exif.longitude is not None:
        return exif.latitude, exif.longitude, LocationSource.EXIF
    raise ObservationError(
        "location_required", "No location: enable GPS or use a photo with GPS data"
    )


def _as_aware(value: datetime | None, utc_offset_minutes: int | None) -> datetime | None:
    """Cameras often write local time without an offset. Assume the device's current
    timezone; fall back to UTC if the client didn't say."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone(timedelta(minutes=utc_offset_minutes or 0)))


def _observed_at(device_time: datetime | None, exif_time: datetime | None) -> datetime:
    now = datetime.now(UTC)
    value = device_time or exif_time or now
    if value > now + MAX_CLOCK_SKEW:
        raise ObservationError("observed_in_future", "Observation time is in the future")
    return value
