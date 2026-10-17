def test_liveness(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readiness_checks_database(client):
    assert client.get("/readyz").json() == {"status": "ready"}
