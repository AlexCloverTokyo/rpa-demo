import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from mock_site.backend.models import Account, Base


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


def test_account_unique_email(db):
    from sqlalchemy.exc import IntegrityError
    a1 = Account(username="alice", email="same@test.com", department="Dev", permissions="[]")
    a2 = Account(username="alice2", email="same@test.com", department="Dev", permissions="[]")
    db.add(a1)
    db.commit()
    db.add(a2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_account_permissions_json(db):
    acct = Account(
        username="bob",
        email="bob@test.com",
        department="Sales",
        permissions=json.dumps(["read", "write"]),
    )
    db.add(acct)
    db.commit()
    fetched = db.query(Account).filter_by(username="bob").first()
    assert json.loads(fetched.permissions) == ["read", "write"]


def test_health(test_client):
    resp = test_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_create_account(test_client):
    resp = test_client.post("/accounts", json={
        "username": "test_user",
        "email": "test@example.com",
        "department": "Dev",
        "permissions": ["read"],
    })
    assert resp.status_code == 201
    assert resp.json()["username"] == "test_user"


def test_create_account_duplicate_email_returns_409(test_client):
    test_client.post("/accounts", json={"username": "dup1", "email": "dup@test.com", "department": "Dev", "permissions": []})
    resp = test_client.post("/accounts", json={"username": "dup2", "email": "dup@test.com", "department": "Dev", "permissions": []})
    assert resp.status_code == 409

def test_create_account_duplicate_username_returns_409(test_client):
    # Username is the API's primary lookup key for every /accounts/{username} endpoint,
    # so duplicate usernames must be rejected to avoid silent misrouting.
    test_client.post("/accounts", json={"username": "samename", "email": "sn1@test.com", "department": "Dev", "permissions": []})
    resp = test_client.post("/accounts", json={"username": "samename", "email": "sn2@test.com", "department": "Dev", "permissions": []})
    assert resp.status_code == 409


def test_get_account_not_found_returns_404(test_client):
    resp = test_client.get("/accounts/nonexistent")
    assert resp.status_code == 404


def test_get_account(test_client):
    test_client.post("/accounts", json={
        "username": "alice", "email": "a@test.com", "department": "Dev", "permissions": ["read"]
    })
    resp = test_client.get("/accounts/alice")
    assert resp.status_code == 200
    assert resp.json()["username"] == "alice"


def test_update_permissions_replaces_existing(test_client):
    test_client.post("/accounts", json={
        "username": "bob", "email": "b@test.com", "department": "Dev", "permissions": ["report"]
    })
    resp = test_client.patch("/accounts/bob/permissions", json={"permissions": ["export"]})
    assert resp.status_code == 200
    assert resp.json()["permissions"] == ["export"]
    assert "report" not in resp.json()["permissions"]


def test_update_permissions_clears_when_empty(test_client):
    test_client.post("/accounts", json={
        "username": "carol", "email": "c@test.com", "department": "Dev",
        "permissions": ["read", "write"],
    })
    resp = test_client.patch("/accounts/carol/permissions", json={"permissions": []})
    assert resp.status_code == 200
    assert resp.json()["permissions"] == []


def test_update_permissions_not_found(test_client):
    resp = test_client.patch("/accounts/nobody/permissions", json={"permissions": ["read"]})
    assert resp.status_code == 404


def test_login_success(test_client):
    resp = test_client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200
    assert "token" in resp.json()


def test_login_failure(test_client):
    resp = test_client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_update_account(test_client):
    test_client.post("/accounts", json={
        "username": "dave", "email": "old@test.com", "department": "OldDept", "permissions": []
    })
    resp = test_client.put("/accounts/dave", json={
        "username": "dave", "email": "new@test.com", "department": "NewDept", "permissions": ["read"]
    })
    assert resp.status_code == 200
    assert resp.json()["email"] == "new@test.com"
    assert resp.json()["department"] == "NewDept"
    assert resp.json()["username"] == "dave"
    assert resp.json()["permissions"] == ["read"]


def test_update_account_rename_username(test_client):
    test_client.post("/accounts", json={
        "username": "dave2", "email": "d2@test.com", "department": "Dev", "permissions": []
    })
    resp = test_client.put("/accounts/dave2", json={
        "username": "dave2_renamed", "email": "d2@test.com", "department": "Dev", "permissions": []
    })
    assert resp.status_code == 200
    assert resp.json()["username"] == "dave2_renamed"


def test_update_account_email_conflict(test_client):
    test_client.post("/accounts", json={
        "username": "dave3", "email": "d3@test.com", "department": "Dev", "permissions": []
    })
    test_client.post("/accounts", json={
        "username": "dave4", "email": "d4@test.com", "department": "Dev", "permissions": []
    })
    resp = test_client.put("/accounts/dave3", json={
        "username": "dave3", "email": "d4@test.com", "department": "Dev", "permissions": []
    })
    assert resp.status_code == 409


def test_update_account_not_found(test_client):
    resp = test_client.put("/accounts/nobody", json={
        "username": "nobody", "email": "x@test.com", "department": "X", "permissions": []
    })
    assert resp.status_code == 404


def test_update_status_to_inactive(test_client):
    test_client.post("/accounts", json={
        "username": "eve", "email": "e@test.com", "department": "Dev", "permissions": []
    })
    resp = test_client.patch("/accounts/eve/status", json={"status": "inactive"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "inactive"


def test_update_status_to_active(test_client):
    test_client.post("/accounts", json={
        "username": "frank", "email": "f@test.com", "department": "Dev", "permissions": []
    })
    test_client.patch("/accounts/frank/status", json={"status": "inactive"})
    resp = test_client.patch("/accounts/frank/status", json={"status": "active"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


def test_update_status_not_found(test_client):
    resp = test_client.patch("/accounts/nobody/status", json={"status": "inactive"})
    assert resp.status_code == 404


def test_delete_account(test_client):
    test_client.post("/accounts", json={
        "username": "grace", "email": "g@test.com", "department": "Dev", "permissions": []
    })
    resp = test_client.delete("/accounts/grace")
    assert resp.status_code == 204
    # Confirm gone
    assert test_client.get("/accounts/grace").status_code == 404


def test_delete_account_not_found(test_client):
    resp = test_client.delete("/accounts/nobody")
    assert resp.status_code == 404
