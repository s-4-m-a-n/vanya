import pytest

from app import handlers
from app.adapters.analytics.postgres import PostgresAnalyticsStore
from app.adapters.species_id import get_species_identifier
from app.main import app
from app.services import verification
from tests.helpers import FakeBaseline, FakeIdentifier, taxon_id, upload


class RecordingStore:
    def __init__(self):
        self.rows, self.forgotten = [], []

    def record_verified(self, row):
        self.rows.append(row)

    def forget(self, observation_id):
        self.forgotten.append(observation_id)


@pytest.fixture
def store(monkeypatch):
    recording = RecordingStore()
    monkeypatch.setattr(handlers, "get_analytics", lambda: recording)
    monkeypatch.setattr(verification, "get_occurrence_source", lambda: FakeBaseline())
    app.dependency_overrides[get_species_identifier] = lambda: FakeIdentifier(
        [("Passer domesticus", 0.95)]
    )
    return recording


def test_verified_records_are_exported_without_personal_data(client, make_user, seeded, store):
    _, headers = make_user()
    obs = upload(client, headers, taxon_id=str(taxon_id(client, "Passer domesticus"))).json()

    assert obs["status"] == "verified"
    row = store.rows[0]
    assert row["observation_id"] == obs["id"]
    assert row["scientific_name"] == "Passer domesticus"
    assert row["method"] == "auto" and row["rarity_score"] == 0.0
    assert not {"user_id", "email", "display_name"} & row.keys()

    client.delete(f"/api/v1/observations/{obs['id']}", headers=headers)
    assert store.forgotten == [obs["id"]]


def test_postgres_store_counts_verified_records_per_cell(client, make_user, seeded, store):
    _, headers = make_user()
    sparrow = str(taxon_id(client, "Passer domesticus"))
    for lat in ("51.51", "51.52", "48.85"):
        upload(client, headers, taxon_id=sparrow, latitude=lat)
    cells = sorted(PostgresAnalyticsStore().species_cells(0.5), key=lambda c: c.cell_lat)
    assert [(c.cell_lat, c.count) for c in cells] == [(48.75, 1), (51.75, 2)]
