import os
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import create_app
from app.database.base import Base
from app.models import APIKey, User
from app.services.api_key_service import validate_api_key
from app.services.auth_service import create_access_token, hash_password


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.fixture()
def test_context(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL test database")

    engine = create_engine(TEST_DATABASE_URL)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    def db_generator():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    import app.middleware.auth as auth_middleware
    import app.routes.api_keys as api_key_routes
    import app.routes.auth as auth_routes

    monkeypatch.setattr(auth_middleware, "get_db", db_generator)
    monkeypatch.setattr(api_key_routes, "get_db", db_generator)
    monkeypatch.setattr(auth_routes, "get_db", db_generator)
    client = create_app().test_client()
    yield client, session_factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def register(client, username="example", email="example@example.com", password="strong-password"):
    return client.post("/api/v1/auth/register", json={"username": username, "email": email, "password": password})


def login(client, email="example@example.com", password="strong-password"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_registration_and_login(test_context):
    client, _ = test_context
    response = register(client)
    assert response.status_code == 201
    assert "password_hash" not in response.get_json()["user"]
    assert login(client).status_code == 200
    assert login(client, password="wrong").status_code == 401
    assert login(client, email="missing@example.com").status_code == 401


def test_registration_validation_and_duplicates(test_context):
    client, _ = test_context
    assert register(client, email="EXAMPLE@example.com").status_code == 201
    assert register(client, username="other").status_code == 409
    assert register(client, username="example").status_code == 409
    assert client.post("/api/v1/auth/register", json={}).status_code == 400


def test_jwt_and_current_user(test_context):
    client, _ = test_context
    register(client)
    token = login(client).get_json()["access_token"]
    assert client.get("/api/v1/auth/me", headers=auth_header(token)).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me", headers=auth_header("invalid")).status_code == 401


def test_inactive_user_cannot_login(test_context):
    client, session_factory = test_context
    register(client)
    session = session_factory()
    try:
        user = session.query(User).first()
        user.is_active = False
        session.commit()
    finally:
        session.close()
    assert login(client).status_code == 401


def test_api_key_lifecycle_and_ownership(test_context):
    client, session_factory = test_context
    register(client)
    token = login(client).get_json()["access_token"]
    created = client.post("/api/v1/api-keys", headers=auth_header(token), json={"name": "Production API"})
    assert created.status_code == 201
    plaintext_key = created.get_json()["api_key"]["key"]
    listed = client.get("/api/v1/api-keys", headers=auth_header(token)).get_json()["api_keys"]
    assert "key" not in listed[0]
    assert "key_hash" not in listed[0]
    key_id = listed[0]["id"]
    session = session_factory()
    try:
        assert validate_api_key(session, plaintext_key) is not None
    finally:
        session.close()
    assert client.delete(f"/api/v1/api-keys/{key_id}", headers=auth_header(token)).status_code == 200

    session = session_factory()
    try:
        api_key = session.get(APIKey, key_id)
        assert api_key.is_active is False
        assert api_key.revoked_at is not None
    finally:
        session.close()


def test_user_cannot_revoke_another_users_key(test_context):
    client, _ = test_context
    register(client)
    first_token = login(client).get_json()["access_token"]
    created = client.post("/api/v1/api-keys", headers=auth_header(first_token), json={"name": "Private"})
    key_id = created.get_json()["api_key"]["id"]
    register(client, username="other", email="other@example.com")
    second_token = login(client, email="other@example.com").get_json()["access_token"]
    assert client.delete(f"/api/v1/api-keys/{key_id}", headers=auth_header(second_token)).status_code == 404


def test_expired_api_key_is_rejected(test_context):
    client, session_factory = test_context
    register(client)
    token = login(client).get_json()["access_token"]
    session = session_factory()
    try:
        user = session.query(User).first()
        plaintext_key = "gf_live_expired-secret"
        api_key = APIKey(
            user_id=user.id,
            name="Expired",
            key_prefix=plaintext_key[:16],
            key_hash=hash_password(plaintext_key),
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(api_key)
        session.commit()
        assert validate_api_key(session, plaintext_key) is None
    finally:
        session.close()
    assert client.get("/api/v1/api-keys", headers=auth_header(token)).status_code == 200


def test_expired_jwt_is_rejected(test_context, monkeypatch):
    client, session_factory = test_context
    register(client)
    session = session_factory()
    user = session.query(User).first()
    monkeypatch.setattr("app.services.auth_service.Config.JWT_EXPIRATION_MINUTES", -1)
    expired_token = create_access_token(user.id)
    session.close()
    assert client.get("/api/v1/auth/me", headers=auth_header(expired_token)).status_code == 401
