import uuid

from app.models import Role


def test_observer_cannot_change_roles(client, make_user):
    target, _ = make_user()
    _, headers = make_user(Role.OBSERVER)
    res = client.patch(
        f"/api/v1/admin/users/{target.id}/role", json={"role": "verifier"}, headers=headers
    )
    assert res.status_code == 403


def test_admin_promotes_user_to_verifier(client, make_user):
    target, target_headers = make_user()
    _, headers = make_user(Role.ADMIN)
    res = client.patch(
        f"/api/v1/admin/users/{target.id}/role", json={"role": "verifier"}, headers=headers
    )
    assert res.status_code == 200
    assert res.json()["role"] == "verifier"
    # Role is read from the DB, so the existing token reflects the change immediately.
    assert client.get("/api/v1/auth/me", headers=target_headers).json()["role"] == "verifier"


def test_admin_cannot_demote_self(client, make_user):
    admin, headers = make_user(Role.ADMIN)
    res = client.patch(
        f"/api/v1/admin/users/{admin.id}/role", json={"role": "observer"}, headers=headers
    )
    assert res.status_code == 400


def test_unknown_user_is_404(client, make_user):
    _, headers = make_user(Role.ADMIN)
    res = client.patch(
        f"/api/v1/admin/users/{uuid.uuid4()}/role", json={"role": "analyst"}, headers=headers
    )
    assert res.status_code == 404
