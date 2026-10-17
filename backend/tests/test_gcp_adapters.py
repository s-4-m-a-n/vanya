"""Contract tests for the Google Cloud adapters with the SDK clients mocked out.

They verify our calls into the SDKs without needing a GCP project. Skipped unless the
`gcp` extra is installed (`uv sync --extra gcp`).
"""

import json
from unittest import mock

import pytest

pytest.importorskip("google.cloud.storage")
pytest.importorskip("google.cloud.pubsub_v1")


def test_gcs_storage_uploads_and_signs_v4_urls():
    with mock.patch("google.cloud.storage.Client") as client_cls:
        from app.adapters.storage.gcs import GcsBlobStorage

        storage = GcsBlobStorage("dh-photos", project="dh-project")
        bucket = client_cls.return_value.bucket.return_value
        client_cls.assert_called_once_with(project="dh-project")

        storage.put("obs/1.jpg", b"img", "image/jpeg")
        bucket.blob.assert_called_with("obs/1.jpg")
        bucket.blob.return_value.upload_from_string.assert_called_once_with(
            b"img", content_type="image/jpeg"
        )

        with pytest.raises(ValueError):
            storage.put("../x.jpg", b"img", "image/jpeg")

        credentials = mock.Mock(service_account_email="api@dh.iam", token="tok")
        with mock.patch("google.auth.default", return_value=(credentials, "dh-project")):
            storage.signed_url("obs/1.jpg", expires_in=60)
        kwargs = bucket.blob.return_value.generate_signed_url.call_args.kwargs
        assert kwargs["version"] == "v4"
        assert kwargs["method"] == "GET"
        assert kwargs["service_account_email"] == "api@dh.iam"
        assert kwargs["access_token"] == "tok"


def test_pubsub_bus_publishes_json_to_prefixed_topic():
    with mock.patch("google.cloud.pubsub_v1.PublisherClient") as publisher_cls:
        from app.adapters.events.pubsub import PubSubEventBus

        publisher = publisher_cls.return_value
        publisher.topic_path.side_effect = lambda p, t: f"projects/{p}/topics/{t}"

        event = PubSubEventBus("dh-project", "dh.").publish("observation.verified", {"id": 7})

        publisher.topic_path.assert_called_once_with("dh-project", "dh.observation.verified")
        args, kwargs = publisher.publish.call_args
        assert args[0] == "projects/dh-project/topics/dh.observation.verified"
        assert json.loads(args[1]) == {"id": 7}
        assert kwargs["event_id"] == event.id
        assert kwargs["topic"] == "observation.verified"  # push envelopes don't carry it
        publisher.publish.return_value.result.assert_called_once()


def test_vertex_gemini_requests_json_with_schema_and_thinking_level():
    pytest.importorskip("google.genai")
    with mock.patch("google.genai.Client") as client_cls:
        from app.adapters.llm import ImageInput
        from app.adapters.llm.vertex import VertexGeminiClient

        generate = client_cls.return_value.models.generate_content
        generate.return_value.text = '{"ok": true}'

        llm = VertexGeminiClient(
            "dh-project", "global", "google/gemini-3-flash-preview", "low", max_output_tokens=2048
        )
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        result = llm.generate_json(
            system="sys",
            prompt="identify",
            schema=schema,
            schema_name="t",
            images=[ImageInput(b"img")],
        )

        assert result == {"ok": True}
        client_cls.assert_called_once_with(vertexai=True, project="dh-project", location="global")
        kwargs = generate.call_args.kwargs
        assert kwargs["model"] == "gemini-3-flash-preview"
        image_part, prompt = kwargs["contents"]
        assert image_part.inline_data.data == b"img" and prompt == "identify"
        config = kwargs["config"]
        assert config.response_mime_type == "application/json"
        assert config.response_json_schema == schema
        assert config.system_instruction == "sys"
        assert config.thinking_config.thinking_level.value == "LOW"
        assert config.max_output_tokens == 2048


def test_bigquery_store_aggregates_cells_and_streams_rows():
    pytest.importorskip("google.cloud.bigquery")
    with mock.patch("google.cloud.bigquery.Client") as client_cls:
        from app.adapters.analytics.bigquery import BigQueryAnalyticsStore

        client = client_cls.return_value
        client.query.return_value.result.return_value = [
            {"lat_cell": 55.0, "lon_cell": 170.0, "taxon_id": 7, "n": 12}
        ]
        client.insert_rows_json.return_value = []

        store = BigQueryAnalyticsStore("dh-project", "biodiversity")
        cells = store.species_cells(0.5)
        assert (cells[0].cell_lat, cells[0].cell_lon, cells[0].count) == (27.75, 85.25, 12)
        sql = client.query.call_args.args[0]
        assert "`dh-project.biodiversity.observations`" in sql and "FLOOR(latitude / @step)" in sql

        store.record_verified({"observation_id": "o1", "taxon_id": 7})
        table, rows = client.insert_rows_json.call_args.args
        assert table == "dh-project.biodiversity.observations" and rows[0]["taxon_id"] == 7
        assert client.insert_rows_json.call_args.kwargs["row_ids"] == ["o1"]  # dedupe retries

        store.forget("o1")
        assert "DELETE FROM" in client.query.call_args.args[0]
