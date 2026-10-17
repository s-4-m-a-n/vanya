import json

from app.cli import DEFAULT_TAXA_FILE, seed_taxa

SEED_COUNT = len(json.loads(DEFAULT_TAXA_FILE.read_text())["taxa"])


def test_seed_is_idempotent(client, seeded):
    seed_taxa(DEFAULT_TAXA_FILE)
    assert client.get("/api/v1/taxa").json()["total"] == SEED_COUNT


def test_search_by_common_name(client, seeded):
    items = client.get("/api/v1/taxa", params={"q": "sparrow"}).json()["items"]
    assert [t["scientific_name"] for t in items] == ["Passer domesticus"]


def test_filter_by_group(client, seeded):
    items = client.get("/api/v1/taxa", params={"group": "plant", "limit": 200}).json()["items"]
    assert items and all(t["taxon_group"] == "plant" for t in items)


def test_localised_name_falls_back_to_english(client, seeded):
    items = client.get("/api/v1/taxa", params={"q": "Panthera", "locale": "hi"}).json()["items"]
    assert items[0]["common_name"] == "बाघ"
    items = client.get("/api/v1/taxa", params={"q": "Ginkgo", "locale": "hi"}).json()["items"]
    assert items[0]["common_name"] == "Ginkgo"


def test_pagination(client, seeded):
    page = client.get("/api/v1/taxa", params={"limit": 5, "offset": 5}).json()
    assert page["total"] == SEED_COUNT
    assert len(page["items"]) == 5


def test_unknown_taxon_is_404(client):
    assert client.get("/api/v1/taxa/999999").status_code == 404


def test_reseeding_keeps_admin_decisions(client, seeded, db):
    from sqlalchemy import select

    from app.models import Taxon

    tiger = db.scalar(select(Taxon).where(Taxon.scientific_name == "Panthera tigris"))
    tiger.iucn_status, tiger.status_source, tiger.sensitivity = "CR", "manual", "hidden"
    db.commit()
    seed_taxa(DEFAULT_TAXA_FILE)
    db.expire_all()
    tiger = db.scalar(select(Taxon).where(Taxon.scientific_name == "Panthera tigris"))
    assert (tiger.iucn_status, tiger.status_source, tiger.sensitivity) == ("CR", "manual", "hidden")
    turtle = db.scalar(select(Taxon).where(Taxon.scientific_name == "Chelonia mydas"))
    assert (turtle.iucn_status, turtle.sensitivity) == ("LC", "obscured")
