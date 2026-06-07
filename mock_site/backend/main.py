import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from .chaos import chaos_middleware
from .models import Account, get_db
from .seed import seed

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("TESTING"):
        seed()
    yield


app = FastAPI(lifespan=lifespan)
app.middleware("http")(chaos_middleware)


def _validate_email(v: str) -> str:
    v = v.strip()
    if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', v):
        raise ValueError('メールアドレスの形式が正しくありません')
    return v


class AccountCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=200)
    department: str = Field(min_length=1, max_length=100)
    permissions: list[str] = []

    @field_validator('email')
    @classmethod
    def validate_email(cls, v: str) -> str:
        return _validate_email(v)


class PermissionUpdate(BaseModel):
    permissions: list[str]


class AccountUpdate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=200)
    department: str = Field(min_length=1, max_length=100)
    permissions: list[str] = []

    @field_validator('email')
    @classmethod
    def validate_email(cls, v: str) -> str:
        return _validate_email(v)


class StatusUpdate(BaseModel):
    status: Literal["active", "inactive"]


class LoginRequest(BaseModel):
    username: str
    password: str


def _to_dict(account: Account) -> dict:
    try:
        permissions = json.loads(account.permissions or "[]")
    except (json.JSONDecodeError, TypeError):
        permissions = []
    return {
        "id": account.id,
        "username": account.username,
        "email": account.email,
        "department": account.department,
        "permissions": permissions,
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "status": account.status,
    }


@app.get("/chaos/status")
def chaos_status():
    from .chaos import _load_config
    cfg = _load_config()
    return {
        "enabled": cfg.get("chaos", {}).get("enabled", False),
        "selector_chaos": cfg.get("selector_chaos", {}).get("enabled", False),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def frontend():
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend not built yet")
    return FileResponse(index)


@app.post("/auth/login")
def login(data: LoginRequest):
    if (
        data.username == os.environ.get("MOCK_SITE_USER", "admin")
        and data.password == os.environ.get("MOCK_SITE_PASSWORD", "admin")
    ):
        return {"token": "demo-token", "status": "ok"}
    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.get("/accounts")
def list_accounts(db: Session = Depends(get_db)):
    return [_to_dict(a) for a in db.query(Account).all()]


@app.get("/accounts/by-email/{email}")
def get_account_by_email(email: str, db: Session = Depends(get_db)):
    acct = db.query(Account).filter(Account.email == email).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    return _to_dict(acct)


@app.get("/accounts/{username}")
def get_account(username: str, db: Session = Depends(get_db)):
    acct = db.query(Account).filter(Account.username == username).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    return _to_dict(acct)


@app.post("/accounts", status_code=201)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    if db.query(Account).filter(Account.username == data.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    if db.query(Account).filter(Account.email == data.email).first():
        raise HTTPException(status_code=409, detail="Email already exists")
    acct = Account(
        username=data.username,
        email=data.email,
        department=data.department,
        permissions=json.dumps(data.permissions),
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return _to_dict(acct)


@app.put("/accounts/{username}")
def update_account(username: str, data: AccountUpdate, db: Session = Depends(get_db)):
    acct = db.query(Account).filter(Account.username == username).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    if data.email != acct.email:
        if db.query(Account).filter(Account.email == data.email).first():
            raise HTTPException(status_code=409, detail="Email already exists")
    acct.username = data.username
    acct.email = data.email
    acct.department = data.department
    acct.permissions = json.dumps(data.permissions)
    db.commit()
    db.refresh(acct)
    return _to_dict(acct)


@app.patch("/accounts/{username}/status")
def update_status(username: str, data: StatusUpdate, db: Session = Depends(get_db)):
    acct = db.query(Account).filter(Account.username == username).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    acct.status = data.status
    db.commit()
    db.refresh(acct)
    return _to_dict(acct)


@app.delete("/accounts/{username}", status_code=204)
def delete_account(username: str, db: Session = Depends(get_db)):
    acct = db.query(Account).filter(Account.username == username).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    db.delete(acct)
    db.commit()


@app.patch("/accounts/{username}/permissions")
def update_permissions(username: str, data: PermissionUpdate, db: Session = Depends(get_db)):
    acct = db.query(Account).filter(Account.username == username).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    acct.permissions = json.dumps(data.permissions)
    db.commit()
    db.refresh(acct)
    return _to_dict(acct)
