from datetime import UTC, datetime

from app.models import Role

REGISTER = {"email": "Ana@Example.org", "password": "s3cret-pass", "display_name": "Ana"}


def test_register_returns_token_and_observer_role(client):
    res = client.post("/api/v1/auth/register", json=REGISTER)
    assert res.status_code == 201
    body = res.json()
    assert body["access_token"]
    assert body["user"]["email"] == "ana@example.org"
    assert body["user"]["role"] == Role.OBSERVER
    assert body["user"]["reputation"] == 0


def test_register_rejects_duplicate_email_case_insensitively(client):
    client.post("/api/v1/auth/register", json=REGISTER)
    res = client.post("/api/v1/auth/register", json={**REGISTER, "email": "ANA@example.org"})
    assert res.status_code == 409


def test_register_validates_password_length(client):
    res = client.post("/api/v1/auth/register", json={**REGISTER, "password": "short"})
    assert res.status_code == 422


def test_login_and_me(client):
    client.post("/api/v1/auth/register", json=REGISTER)
    res = client.post(
        "/api/v1/auth/login", json={"email": "ana@example.org", "password": "s3cret-pass"}
    )
    assert res.status_code == 200
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["display_name"] == "Ana"


def test_login_rejects_wrong_password(client):
    client.post("/api/v1/auth/register", json=REGISTER)
    res = client.post(
        "/api/v1/auth/login", json={"email": REGISTER["email"], "password": "nope1234"}
    )
    assert res.status_code == 401


def test_me_requires_valid_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    bad = {"Authorization": "Bearer not-a-jwt"}
    assert client.get("/api/v1/auth/me", headers=bad).status_code == 401


def test_deleted_user_token_is_rejected(client, db, make_user):
    user, headers = make_user()
    user.deleted_at = datetime.now(UTC)
    db.commit()
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_profile_update_records_and_withdraws_location_consent(client, make_user):
    _, headers = make_user(consent=False)
    res = client.patch(
        "/api/v1/auth/me", json={"location_consent": True, "locale": "hi"}, headers=headers
    )
    assert res.status_code == 200
    assert res.json()["location_consent_at"] is not None and res.json()["locale"] == "hi"

    res = client.patch("/api/v1/auth/me", json={"location_consent": False}, headers=headers)
    assert res.json()["location_consent_at"] is None
