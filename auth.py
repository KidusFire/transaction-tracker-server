import secrets
import bcrypt
from datetime import datetime
from fastapi import Header, HTTPException, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from database import get_db
import models

security = HTTPBasic()

# Plan limits: max number of active employees allowed per plan. None = unlimited.
PLAN_LIMITS = {
    "free": 1,
    "starter": 10,
    "growth": 25,
    "enterprise": None,
}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def generate_api_key() -> str:
    return secrets.token_hex(24)


def get_company_from_api_key(x_api_key: str = Header(...), db: Session = Depends(get_db)) -> models.Company:
    """Identifies which company an employee app belongs to."""
    company = db.query(models.Company).filter(models.Company.api_key == x_api_key).first()
    if not company:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not company.is_active:
        raise HTTPException(status_code=403, detail="This account is inactive")
    if company.plan == "free" and company.trial_ends_at and datetime.utcnow() > company.trial_ends_at:
        raise HTTPException(
            status_code=403,
            detail="Your 30-day free trial has ended. Upgrade your plan to keep logging transactions and inventory."
        )
    return company


def get_company_from_dashboard_login(
    credentials: HTTPBasicCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> models.Company:
    """Used by the business owner's dashboard — full access to their company's data."""
    company = db.query(models.Company).filter(
        models.Company.dashboard_username == credentials.username
    ).first()

    if not company or not verify_password(credentials.password, company.dashboard_password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials", headers={"WWW-Authenticate": "Basic"})

    return company


def verify_employee(company: models.Company, username: str, password: str, db: Session) -> models.Employee:
    """Verifies an individual employee's own login, scoped to their company."""
    employee = db.query(models.Employee).filter(
        models.Employee.company_id == company.id,
        models.Employee.username == username,
        models.Employee.active == True
    ).first()

    if not employee or not verify_password(password, employee.password_hash):
        raise HTTPException(status_code=401, detail="Invalid employee credentials")

    return employee


def check_employee_limit(company: models.Company, db: Session):
    limit = PLAN_LIMITS.get(company.plan, 3)
    if limit is None:
        return
    current_count = db.query(models.Employee).filter(
        models.Employee.company_id == company.id,
        models.Employee.active == True
    ).count()
    if current_count >= limit:
        raise HTTPException(
            status_code=403,
            detail=f"Your '{company.plan}' plan allows up to {limit} employees. Upgrade your plan to add more."
        )
