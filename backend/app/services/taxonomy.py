"""Map identifier output (free-text scientific names) onto the taxa table."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import IucnStatus, Taxon, TaxonGroup, TaxonSource
from app.services.gbif import GbifClient

# v1 covers birds, plants, and tetrapod animals; insects, fish and fungi are non-goals
# (PRD §3). GBIF's backbone uses both "Reptilia" and its split classes.
ANIMAL_CLASSES = {"Mammalia", "Amphibia", "Reptilia", "Squamata", "Testudines", "Crocodylia"}


def taxon_group_for(kingdom: str | None, class_name: str | None) -> TaxonGroup | None:
    if kingdom == "Plantae":
        return TaxonGroup.PLANT
    if kingdom == "Animalia" and class_name == "Aves":
        return TaxonGroup.BIRD
    if kingdom == "Animalia" and class_name in ANIMAL_CLASSES:
        return TaxonGroup.ANIMAL
    return None


@dataclass(frozen=True)
class Resolution:
    taxon: Taxon | None
    in_scope: bool


class TaxonResolver:
    def __init__(self, db: Session, gbif: GbifClient | None) -> None:
        self.db = db
        self.gbif = gbif

    def resolve(
        self,
        scientific_name: str,
        common_name: str | None,
        locale: str,
        group_hint: str,
        english_name: str | None = None,
    ) -> Resolution:
        names = {k: v for k, v in (("en", english_name), (locale, common_name)) if v}
        taxon = self._by_name(scientific_name)
        if taxon is not None:
            return Resolution(taxon, in_scope=True)
        if self.gbif is None:
            # Without GBIF we can only trust the identifier's own scope judgement.
            return Resolution(None, in_scope=group_hint != "other")

        match = self.gbif.match_species(scientific_name)
        if match is None:
            return Resolution(None, in_scope=group_hint != "other")
        group = taxon_group_for(match.kingdom, match.class_name)
        if group is None:
            return Resolution(None, in_scope=False)

        taxon = self.db.scalar(select(Taxon).where(Taxon.gbif_key == match.species_key))
        taxon = taxon or self._by_name(match.species)
        if taxon is not None:
            if taxon.gbif_key is None:
                taxon.gbif_key = match.species_key
            self._add_common_names(taxon, names)
            return Resolution(taxon, in_scope=True)

        self.db.execute(
            insert(Taxon)
            .values(
                scientific_name=match.species,
                common_names=names,
                taxon_group=group.value,
                kingdom=match.kingdom,
                class_name=match.class_name,
                family=match.family,
                iucn_status=IucnStatus.NOT_EVALUATED.value,
                gbif_key=match.species_key,
                source=TaxonSource.GBIF.value,
            )
            .on_conflict_do_nothing()
        )
        return Resolution(self._by_name(match.species), in_scope=True)

    def _by_name(self, name: str) -> Taxon | None:
        return self.db.scalar(
            select(Taxon).where(func.lower(Taxon.scientific_name) == name.lower())
        )

    @staticmethod
    def _add_common_names(taxon: Taxon, names: dict[str, str]) -> None:
        """Fill in missing locales; never overwrite curated names."""
        missing = {k: v for k, v in names.items() if k not in taxon.common_names}
        if missing:
            taxon.common_names = {**taxon.common_names, **missing}
