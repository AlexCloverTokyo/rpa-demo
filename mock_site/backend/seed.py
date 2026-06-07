import json

from faker import Faker

from .models import Account, SessionLocal, init_db

fake = Faker("ja_JP")
Faker.seed(42)

DEPARTMENTS = ["営業部", "開発部", "人事部", "経理部", "マーケティング部"]
PERMISSION_SETS = [[], ["report"], ["export"], ["approver"], ["report", "export"], ["report", "approver"], ["export", "approver"], ["report", "export", "approver"]]


def seed():
    init_db()
    db = SessionLocal()
    try:
        if db.query(Account).count() > 0:
            return
        for _ in range(20):
            db.add(Account(
                username=fake.user_name(),
                email=fake.email(),
                department=fake.random_element(DEPARTMENTS),
                permissions=json.dumps(fake.random_element(PERMISSION_SETS)),
            ))
        db.commit()
    finally:
        db.close()
