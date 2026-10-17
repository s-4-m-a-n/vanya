from app.config import Settings


def test_cors_origins_parse_from_csv(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    assert Settings().cors_origins == ["http://a.test", "http://b.test"]


def test_production_refuses_placeholder_secrets(monkeypatch):
    import pytest
    from pydantic import ValidationError

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "change-me-in-production")
    with pytest.raises(ValidationError, match="jwt_secret"):
        Settings()
