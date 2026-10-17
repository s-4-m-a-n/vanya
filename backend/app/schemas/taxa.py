from pydantic import BaseModel

from app.models import IucnStatus, Taxon, TaxonGroup


class TaxonOut(BaseModel):
    id: int
    scientific_name: str
    common_name: str | None
    common_names: dict[str, str]
    taxon_group: TaxonGroup
    kingdom: str
    class_name: str | None
    family: str | None
    iucn_status: IucnStatus
    gbif_key: int | None

    @classmethod
    def build(cls, taxon: Taxon, locale: str) -> "TaxonOut":
        return cls(
            id=taxon.id,
            scientific_name=taxon.scientific_name,
            common_name=taxon.common_name(locale),
            common_names=taxon.common_names,
            taxon_group=TaxonGroup(taxon.taxon_group),
            kingdom=taxon.kingdom,
            class_name=taxon.class_name,
            family=taxon.family,
            iucn_status=IucnStatus(taxon.iucn_status),
            gbif_key=taxon.gbif_key,
        )


class TaxonPage(BaseModel):
    items: list[TaxonOut]
    total: int
