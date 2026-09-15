from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from typing import List, Optional
from datetime import datetime, timedelta
import json
import uuid
import hmac
import hashlib
import os

from database import engine, get_db
import models
import schemas
import auth
import chapa
import documents

models.Base.metadata.create_all(bind=engine)

# Lightweight migration: add any new columns that existing deployments' databases
# don't have yet (create_all only creates missing TABLES, not missing COLUMNS on
# tables that already exist). Safe to run every startup — does nothing if already applied.
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS resulting_quantity_on_hand FLOAT"))
        conn.commit()
    except Exception:
        pass  # e.g. SQLite locally doesn't support IF NOT EXISTS here — harmless to skip

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS currency VARCHAR DEFAULT 'USD'"))
        conn.commit()
    except Exception:
        pass

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS receipt_image TEXT"))
        conn.execute(text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS receipt_mime VARCHAR"))
        conn.commit()
    except Exception:
        pass

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS currency VARCHAR DEFAULT 'USD'"))
        conn.commit()
    except Exception:
        pass

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS category VARCHAR DEFAULT 'raw_material'"))
        conn.commit()
    except Exception:
        pass

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS recovery_key_hash VARCHAR"))
        conn.commit()
    except Exception:
        pass

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP"))
        conn.commit()
    except Exception:
        pass

models.Base.metadata.create_all(bind=engine)  # picks up the new PendingPayment / SalesOrder tables

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS bank_details TEXT"))
        conn.commit()
    except Exception:
        pass

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_image TEXT"))
        conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_mime VARCHAR"))
        conn.commit()
    except Exception:
        pass

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS validity_days INTEGER DEFAULT 30"))
        conn.execute(text("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS delivery_terms VARCHAR"))
        conn.execute(text("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS downpayment_percent FLOAT"))
        conn.execute(text("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS payment_terms VARCHAR"))
        conn.execute(text("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS vat_percent FLOAT DEFAULT 15.0"))
        conn.commit()
    except Exception:
        pass

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS acquisition_date TIMESTAMP"))
        conn.execute(text("ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS location VARCHAR"))
        conn.execute(text("ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS serial_number VARCHAR"))
        conn.execute(text("ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS condition VARCHAR"))
        conn.execute(text("ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS useful_life_years FLOAT"))
        conn.execute(text("ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS salvage_value FLOAT DEFAULT 0"))
        conn.commit()
    except Exception:
        pass

app = FastAPI(title="Company Transaction & Inventory Tracker")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/company/me", response_model=schemas.CompanyOut)
def get_my_company(company: models.Company = Depends(auth.get_company_from_dashboard_login)):
    return company


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(company: models.Company = Depends(auth.get_company_from_dashboard_login)):
    with open("static/dashboard.html", encoding="utf-8") as f:
        return f.read()


@app.get("/", response_class=HTMLResponse)
def landing_page():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/terms", response_class=HTMLResponse)
def terms_page():
    with open("static/terms.html", encoding="utf-8") as f:
        return f.read()


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page():
    with open("static/privacy.html", encoding="utf-8") as f:
        return f.read()


@app.get("/support", response_class=HTMLResponse)
def support_page():
    with open("static/support.html", encoding="utf-8") as f:
        return f.read()


@app.get("/signup", response_class=HTMLResponse)
def signup_page():
    with open("static/signup.html", encoding="utf-8") as f:
        return f.read()


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page():
    with open("static/reset-password.html", encoding="utf-8") as f:
        return f.read()


# ============ COMPANY SIGNUP ============

@app.post("/companies/signup", response_model=schemas.CompanySignupOut)
def signup(payload: schemas.CompanySignup, db: Session = Depends(get_db)):
    if payload.plan not in auth.PLAN_LIMITS:
        raise HTTPException(status_code=400, detail=f"Unknown plan. Choose from: {list(auth.PLAN_LIMITS.keys())}")

    existing = db.query(models.Company).filter(
        models.Company.dashboard_username == payload.dashboard_username
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="That dashboard username is already taken")

    recovery_key = auth.generate_api_key()

    # Free plan accounts get a 30-day trial — after that, the employee app is blocked
    # until they upgrade. Paid plans (starter/growth/enterprise) never expire this way.
    trial_ends_at = datetime.utcnow() + timedelta(days=30) if payload.plan == "free" else None

    company = models.Company(
        name=payload.company_name,
        api_key=auth.generate_api_key(),
        dashboard_username=payload.dashboard_username,
        dashboard_password_hash=auth.hash_password(payload.dashboard_password),
        plan=payload.plan,
        currency=payload.currency,
        recovery_key_hash=auth.hash_password(recovery_key),
        trial_ends_at=trial_ends_at,
    )
    db.add(company)
    db.commit()
    db.refresh(company)

    return schemas.CompanySignupOut(
        company_id=company.id, company_name=company.name, api_key=company.api_key,
        dashboard_username=company.dashboard_username, plan=company.plan, currency=company.currency,
        recovery_key=recovery_key,
    )


@app.post("/companies/reset-password")
def reset_password(payload: schemas.PasswordResetRequest, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter(
        models.Company.dashboard_username == payload.dashboard_username
    ).first()

    if not company or not company.recovery_key_hash or not auth.verify_password(payload.recovery_key, company.recovery_key_hash):
        raise HTTPException(status_code=401, detail="Username or recovery key is incorrect")

    company.dashboard_password_hash = auth.hash_password(payload.new_password)
    db.add(company)
    db.commit()

    return {"status": "password reset — you can log in with your new password now"}


@app.post("/company/recovery-key/regenerate", response_model=schemas.RecoveryKeyOut)
def regenerate_recovery_key(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    """For accounts created before recovery keys existed, or if the old one was lost.
    Generating a new one invalidates any previous recovery key."""
    new_key = auth.generate_api_key()
    company.recovery_key_hash = auth.hash_password(new_key)
    db.add(company)
    db.commit()
    return schemas.RecoveryKeyOut(recovery_key=new_key)


# ============ BILLING (Chapa) ============

@app.post("/billing/checkout", response_model=schemas.BillingCheckoutResponse)
def start_checkout(
    payload: schemas.BillingCheckoutRequest,
    request: Request,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    if payload.plan not in auth.PLAN_PRICES_ETB:
        raise HTTPException(status_code=400, detail=f"Choose one of: {list(auth.PLAN_PRICES_ETB.keys())}")

    amount = auth.PLAN_PRICES_ETB[payload.plan]
    tx_ref = f"company-{company.id}-{uuid.uuid4().hex[:12]}"

    pending = models.PendingPayment(
        company_id=company.id, tx_ref=tx_ref, plan=payload.plan, amount_etb=amount, status="pending"
    )
    db.add(pending)
    db.commit()

    base_url = str(request.base_url).rstrip("/")

    try:
        result = chapa.initialize_checkout(
            amount_etb=amount, tx_ref=tx_ref, customer_email=payload.customer_email,
            first_name=company.dashboard_username,
            callback_url=f"{base_url}/billing/webhook",
            return_url=f"{base_url}/billing/return?tx_ref={tx_ref}",
            title=f"{payload.plan.title()} Plan",
            description=f"{payload.plan.title()} plan subscription - {company.name}",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not start payment: {e}")

    checkout_url = result.get("data", {}).get("checkout_url")
    if not checkout_url:
        raise HTTPException(status_code=502, detail="Chapa did not return a checkout URL")

    return schemas.BillingCheckoutResponse(checkout_url=checkout_url)


def _apply_successful_payment(db: Session, pending: models.PendingPayment):
    """Shared logic: called from both the webhook and the manual verify fallback,
    so a payment only ever gets applied once (idempotent)."""
    if pending.status == "success":
        return  # already applied, e.g. webhook and manual verify both fired

    company = db.query(models.Company).filter(models.Company.id == pending.company_id).first()
    if not company:
        return

    company.plan = pending.plan
    company.trial_ends_at = datetime.utcnow() + timedelta(days=30)
    company.is_active = True
    db.add(company)

    pending.status = "success"
    pending.completed_at = datetime.utcnow()
    db.add(pending)
    db.commit()


@app.post("/billing/webhook")
async def billing_webhook(request: Request, db: Session = Depends(get_db)):
    """Chapa calls this automatically when a payment completes. Configure this URL
    (https://your-domain/billing/webhook) in Chapa's dashboard under Settings > Webhooks."""
    body = await request.body()

    webhook_secret = os.getenv("CHAPA_WEBHOOK_SECRET", "")
    if webhook_secret:
        signature = request.headers.get("Chapa-Signature", "")
        expected = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    tx_ref = payload.get("tx_ref")
    if not tx_ref:
        raise HTTPException(status_code=400, detail="Missing tx_ref")

    # Don't trust the webhook body alone — ask Chapa directly to confirm the payment really succeeded.
    try:
        verification = chapa.verify_transaction(tx_ref)
    except Exception:
        raise HTTPException(status_code=502, detail="Could not verify with Chapa")

    if verification.get("data", {}).get("status") != "success":
        return {"status": "ignored — payment not successful"}

    pending = db.query(models.PendingPayment).filter(models.PendingPayment.tx_ref == tx_ref).first()
    if not pending:
        raise HTTPException(status_code=404, detail="Unknown transaction reference")

    _apply_successful_payment(db, pending)

    await manager.broadcast_to_company(pending.company_id, {"kind": "plan_changed"})

    return {"status": "ok"}


@app.get("/billing/verify/{tx_ref}")
def verify_payment_manually(
    tx_ref: str,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    """Fallback for the return page — in case the webhook hasn't arrived yet by the
    time the customer is redirected back from Chapa."""
    pending = db.query(models.PendingPayment).filter(
        models.PendingPayment.tx_ref == tx_ref, models.PendingPayment.company_id == company.id
    ).first()
    if not pending:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if pending.status != "success":
        try:
            verification = chapa.verify_transaction(tx_ref)
        except Exception:
            raise HTTPException(status_code=502, detail="Could not verify with Chapa")

        if verification.get("data", {}).get("status") == "success":
            _apply_successful_payment(db, pending)

    return {"status": pending.status, "plan": pending.plan}


@app.get("/billing/return", response_class=HTMLResponse)
def billing_return_page():
    with open("static/billing-return.html", encoding="utf-8") as f:
        return f.read()


# ============ SALES ORDERS (document lifecycle: Proforma -> Invoice -> ... ) ============

@app.post("/sales-orders", response_model=schemas.SalesOrderOut)
def create_sales_order(
    payload: schemas.SalesOrderCreate,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_api_key)
):
    employee = auth.verify_employee(company, payload.employee_username, payload.employee_password, db)

    if not payload.line_items:
        raise HTTPException(status_code=400, detail="A sales order needs at least one line item")

    order = models.SalesOrder(
        company_id=company.id, employee_id=employee.username,
        customer_name=payload.customer_name, customer_contact=payload.customer_contact,
        currency=payload.currency, note=payload.note, status="proforma",
        validity_days=payload.validity_days, delivery_terms=payload.delivery_terms,
        downpayment_percent=payload.downpayment_percent, payment_terms=payload.payment_terms,
        vat_percent=payload.vat_percent,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    for li in payload.line_items:
        db.add(models.SalesOrderLineItem(
            sales_order_id=order.id, description=li.description,
            quantity=li.quantity, unit_price=li.unit_price,
        ))
    db.commit()

    line_items = db.query(models.SalesOrderLineItem).filter(models.SalesOrderLineItem.sales_order_id == order.id).all()

    return schemas.SalesOrderOut(
        id=order.id, employee_id=order.employee_id, customer_name=order.customer_name,
        customer_contact=order.customer_contact, currency=order.currency, status=order.status,
        note=order.note, validity_days=order.validity_days, delivery_terms=order.delivery_terms,
        downpayment_percent=order.downpayment_percent, payment_terms=order.payment_terms,
        vat_percent=order.vat_percent,
        created_at=order.created_at, updated_at=order.updated_at,
        line_items=[schemas.LineItemOut.model_validate(li) for li in line_items],
    )


@app.get("/sales-orders", response_model=List[schemas.SalesOrderOut])
def list_sales_orders(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    orders = db.query(models.SalesOrder).filter(
        models.SalesOrder.company_id == company.id
    ).order_by(models.SalesOrder.created_at.desc()).all()

    result = []
    for order in orders:
        line_items = db.query(models.SalesOrderLineItem).filter(
            models.SalesOrderLineItem.sales_order_id == order.id
        ).all()
        result.append(schemas.SalesOrderOut(
            id=order.id, employee_id=order.employee_id, customer_name=order.customer_name,
            customer_contact=order.customer_contact, currency=order.currency, status=order.status,
            note=order.note, validity_days=order.validity_days, delivery_terms=order.delivery_terms,
            downpayment_percent=order.downpayment_percent, payment_terms=order.payment_terms,
            vat_percent=order.vat_percent,
            created_at=order.created_at, updated_at=order.updated_at,
            line_items=[schemas.LineItemOut.model_validate(li) for li in line_items],
        ))
    return result


@app.put("/sales-orders/{order_id}/confirm", response_model=schemas.SalesOrderOut)
async def confirm_sales_order(
    order_id: int,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    """Owner-only: moves an order from 'proforma' (quote) to 'confirmed' (firm order),
    which unlocks the Sales Invoice document — the second stage of the paper trail."""
    order = db.query(models.SalesOrder).filter(
        models.SalesOrder.id == order_id, models.SalesOrder.company_id == company.id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sales order not found")
    if order.status != "proforma":
        raise HTTPException(status_code=400, detail=f"Order is already '{order.status}', not a proforma awaiting confirmation")

    order.status = "confirmed"
    order.updated_at = datetime.utcnow()
    db.add(order)
    db.commit()
    db.refresh(order)

    line_items = db.query(models.SalesOrderLineItem).filter(models.SalesOrderLineItem.sales_order_id == order.id).all()

    await manager.broadcast_to_company(company.id, {"kind": "sales_orders_changed"})

    return schemas.SalesOrderOut(
        id=order.id, employee_id=order.employee_id, customer_name=order.customer_name,
        customer_contact=order.customer_contact, currency=order.currency, status=order.status,
        note=order.note, validity_days=order.validity_days, delivery_terms=order.delivery_terms,
        downpayment_percent=order.downpayment_percent, payment_terms=order.payment_terms,
        vat_percent=order.vat_percent,
        created_at=order.created_at, updated_at=order.updated_at,
        line_items=[schemas.LineItemOut.model_validate(li) for li in line_items],
    )


@app.put("/company/bank-details")
def update_bank_details(
    payload: schemas.CompanyBankDetailsUpdate,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    """Set once — reused on every generated document (Proforma, Invoice, etc.)."""
    company.bank_details = payload.bank_details
    db.add(company)
    db.commit()
    return {"status": "saved"}


@app.put("/company/logo")
def update_logo(
    payload: schemas.CompanyLogoUpdate,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    """Set once — appears in the header of every generated document from now on."""
    company.logo_image = payload.logo_image
    company.logo_mime = payload.logo_mime
    db.add(company)
    db.commit()
    return {"status": "saved"}


@app.get("/sales-orders/{order_id}/document/proforma")
def download_proforma(
    order_id: int,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    order = db.query(models.SalesOrder).filter(
        models.SalesOrder.id == order_id, models.SalesOrder.company_id == company.id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sales order not found")

    line_items = db.query(models.SalesOrderLineItem).filter(
        models.SalesOrderLineItem.sales_order_id == order.id
    ).all()

    pdf_bytes = documents.generate_document_pdf(
        "PROFORMA INVOICE (QUOTE)", company.name, order, line_items, company.bank_details,
        company.logo_image, company.logo_mime,
    )

    from fastapi.responses import Response
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="Proforma_SO-{order.id}.pdf"'}
    )


@app.get("/sales-orders/{order_id}/document/invoice")
def download_invoice(
    order_id: int,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    order = db.query(models.SalesOrder).filter(
        models.SalesOrder.id == order_id, models.SalesOrder.company_id == company.id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sales order not found")
    if order.status == "proforma":
        raise HTTPException(status_code=400, detail="Confirm this order first — an Invoice is only available once it's a firm order")

    line_items = db.query(models.SalesOrderLineItem).filter(
        models.SalesOrderLineItem.sales_order_id == order.id
    ).all()

    pdf_bytes = documents.generate_document_pdf(
        "SALES INVOICE", company.name, order, line_items, company.bank_details,
        company.logo_image, company.logo_mime,
    )

    from fastapi.responses import Response
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="Invoice_SO-{order.id}.pdf"'}
    )


@app.post("/employees", response_model=schemas.EmployeeOut)
def create_employee(
    payload: schemas.EmployeeCreate,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    auth.check_employee_limit(company, db)

    existing = db.query(models.Employee).filter(
        models.Employee.company_id == company.id,
        models.Employee.username == payload.username
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="That username is already used in your company")

    employee = models.Employee(
        company_id=company.id,
        username=payload.username,
        password_hash=auth.hash_password(payload.password),
        full_name=payload.full_name,
    )
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee


@app.get("/employees", response_model=List[schemas.EmployeeOut])
def list_employees(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    return db.query(models.Employee).filter(models.Employee.company_id == company.id).all()


@app.delete("/employees/{employee_id}")
def deactivate_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    employee = db.query(models.Employee).filter(
        models.Employee.id == employee_id, models.Employee.company_id == company.id
    ).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    employee.active = False
    db.add(employee)
    db.commit()
    return {"status": "deactivated"}


# ============ WEBSOCKET MANAGER (scoped per company) ============

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[tuple] = []

    async def connect(self, websocket: WebSocket, company_id: int):
        await websocket.accept()
        self.active_connections.append((company_id, websocket))

    def disconnect(self, websocket: WebSocket):
        self.active_connections = [c for c in self.active_connections if c[1] != websocket]

    async def broadcast_to_company(self, company_id: int, message: dict):
        for cid, connection in self.active_connections:
            if cid == company_id:
                await connection.send_text(json.dumps(message, default=str))

manager = ConnectionManager()


# ============ TRANSACTIONS ============

@app.post("/transactions", response_model=schemas.TransactionOut)
async def create_transaction(
    tx: schemas.TransactionCreate,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_api_key)
):
    employee = auth.verify_employee(company, tx.employee_username, tx.employee_password, db)

    db_tx = models.Transaction(
        company_id=company.id, employee_id=employee.username, type=tx.type,
        amount=tx.amount, category=tx.category, note=tx.note,
        created_at=tx.created_at or datetime.utcnow(), synced_from_offline=tx.synced_from_offline,
        receipt_image=tx.receipt_image, receipt_mime=tx.receipt_mime,
        currency=tx.currency or company.currency or "USD",
    )
    db.add(db_tx)
    db.commit()
    db.refresh(db_tx)

    await manager.broadcast_to_company(company.id, {
        "kind": "transaction",
        "id": db_tx.id, "employee_id": db_tx.employee_id, "type": db_tx.type,
        "amount": db_tx.amount, "category": db_tx.category, "note": db_tx.note,
        "created_at": db_tx.created_at, "synced_from_offline": db_tx.synced_from_offline,
        "receipt_mime": db_tx.receipt_mime, "currency": db_tx.currency,
    })
    return db_tx


@app.get("/transactions/{tx_id}/receipt")
def get_receipt(
    tx_id: int,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    tx = db.query(models.Transaction).filter(
        models.Transaction.id == tx_id, models.Transaction.company_id == company.id
    ).first()
    if not tx or not tx.receipt_image:
        raise HTTPException(status_code=404, detail="No receipt found for this transaction")

    return {"receipt_image": tx.receipt_image, "receipt_mime": tx.receipt_mime}


@app.put("/transactions/{tx_id}", response_model=schemas.TransactionOut)
async def update_transaction(
    tx_id: int,
    payload: schemas.TransactionUpdate,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    tx = db.query(models.Transaction).filter(
        models.Transaction.id == tx_id, models.Transaction.company_id == company.id
    ).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    update_data = payload.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tx, field, value)

    db.add(tx)
    db.commit()
    db.refresh(tx)

    await manager.broadcast_to_company(company.id, {"kind": "transactions_changed"})

    return tx


@app.delete("/transactions/{tx_id}")
async def delete_transaction(
    tx_id: int,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    tx = db.query(models.Transaction).filter(
        models.Transaction.id == tx_id, models.Transaction.company_id == company.id
    ).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    db.delete(tx)
    db.commit()

    await manager.broadcast_to_company(company.id, {"kind": "transactions_changed"})

    return {"status": "deleted"}


@app.get("/transactions/mine", response_model=List[schemas.TransactionOut])
def my_transactions(
    employee_username: str,
    employee_password: str,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_api_key)
):
    employee = auth.verify_employee(company, employee_username, employee_password, db)
    return db.query(models.Transaction).filter(
        models.Transaction.company_id == company.id,
        models.Transaction.employee_id == employee.username
    ).order_by(models.Transaction.created_at.desc()).limit(200).all()


@app.get("/transactions", response_model=List[schemas.TransactionOut])
def list_transactions(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    return db.query(models.Transaction).filter(models.Transaction.company_id == company.id)\
        .order_by(models.Transaction.created_at.desc()).all()


@app.get("/summary/range")
def summary_range(
    start_date: str,
    end_date: str,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    """start_date and end_date as YYYY-MM-DD. Returns totals and a daily breakdown,
    both grouped by currency — amounts in different currencies are never added together."""
    from datetime import datetime, timedelta

    try:
        start = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date) + timedelta(days=1)  # include the whole end day
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be in YYYY-MM-DD format")

    transactions = db.query(models.Transaction).filter(
        models.Transaction.company_id == company.id,
        models.Transaction.created_at >= start,
        models.Transaction.created_at < end
    ).order_by(models.Transaction.created_at).all()

    daily = {}    # (date, currency) -> {income, expense}
    totals = {}   # currency -> {income, expense}

    for tx in transactions:
        cur = tx.currency or company.currency or "USD"
        day = tx.created_at.date().isoformat()
        key = (day, cur)
        daily.setdefault(key, {"income": 0, "expense": 0})
        daily[key][tx.type] += tx.amount
        totals.setdefault(cur, {"income": 0, "expense": 0})
        totals[cur][tx.type] += tx.amount

    daily_breakdown = [
        {"date": day, "currency": cur, "income": vals["income"], "expense": vals["expense"],
         "net": vals["income"] - vals["expense"]}
        for (day, cur), vals in sorted(daily.items())
    ]

    totals_out = {
        cur: {"income": vals["income"], "expense": vals["expense"], "net": vals["income"] - vals["expense"]}
        for cur, vals in totals.items()
    }

    transaction_list = [
        {
            "id": tx.id, "employee_id": tx.employee_id, "type": tx.type,
            "amount": tx.amount, "category": tx.category, "note": tx.note,
            "created_at": tx.created_at, "currency": tx.currency or company.currency or "USD",
        }
        for tx in transactions
    ]

    return {
        "totals": totals_out,
        "daily": daily_breakdown,
        "transactions": transaction_list,
    }


@app.get("/summary/today")
def summary_today(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    """Grouped by currency — e.g. {"USD": {"income":.., "expense":.., "net":..}, "ETB": {...}}."""
    start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.query(models.Transaction).filter(
        models.Transaction.company_id == company.id, models.Transaction.created_at >= start_of_day
    ).all()

    grouped = {}
    for tx in rows:
        cur = tx.currency or company.currency or "USD"
        grouped.setdefault(cur, {"income": 0, "expense": 0})
        grouped[cur][tx.type] += tx.amount

    return {
        cur: {"income": v["income"], "expense": v["expense"], "net": v["income"] - v["expense"]}
        for cur, v in grouped.items()
    }


# ============ INVENTORY ============

@app.post("/inventory/items", response_model=schemas.InventoryItemOut)
def create_item(
    item: schemas.InventoryItemCreate,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_api_key)
):
    existing = db.query(models.InventoryItem).filter(
        models.InventoryItem.company_id == company.id, models.InventoryItem.sku == item.sku
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="SKU already exists for this company")

    db_item = models.InventoryItem(**item.dict(), company_id=company.id)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


@app.put("/inventory/items/{item_id}", response_model=schemas.InventoryItemOut)
async def update_item(
    item_id: int,
    payload: schemas.InventoryItemUpdate,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    item = db.query(models.InventoryItem).filter(
        models.InventoryItem.id == item_id, models.InventoryItem.company_id == company.id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")

    update_data = payload.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(item, field, value)

    db.add(item)
    db.commit()
    db.refresh(item)

    await manager.broadcast_to_company(company.id, {
        "kind": "item_updated",
        "id": item.id, "sku": item.sku, "name": item.name, "unit": item.unit,
        "category": item.category,
        "quantity_on_hand": item.quantity_on_hand, "reorder_level": item.reorder_level,
        "unit_cost": item.unit_cost,
        "acquisition_date": item.acquisition_date.isoformat() if item.acquisition_date else None,
        "location": item.location, "serial_number": item.serial_number,
        "condition": item.condition, "useful_life_years": item.useful_life_years,
        "salvage_value": item.salvage_value,
    })

    return item


@app.delete("/inventory/items/{item_id}")
async def delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    item = db.query(models.InventoryItem).filter(
        models.InventoryItem.id == item_id, models.InventoryItem.company_id == company.id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")

    if item.quantity_on_hand != 0:
        raise HTTPException(
            status_code=400,
            detail=f"This item still has {item.quantity_on_hand} {item.unit} in stock. "
                   f"Log a stock movement to bring it to zero before deleting, so the value isn't silently lost."
        )

    open_reqs = db.query(models.Requisition).filter(
        models.Requisition.inventory_item_id == item_id, models.Requisition.status == "open"
    ).count()
    if open_reqs > 0:
        raise HTTPException(status_code=400, detail="This item has an open requisition. Close it first.")

    db.delete(item)
    db.commit()

    await manager.broadcast_to_company(company.id, {"kind": "item_deleted", "id": item_id})

    return {"status": "deleted"}


@app.get("/inventory/items/lookup", response_model=List[schemas.InventoryItemOut])
def lookup_items(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_api_key)
):
    return db.query(models.InventoryItem).filter(models.InventoryItem.company_id == company.id).all()


@app.get("/inventory/items", response_model=List[schemas.InventoryItemOut])
def list_items(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    return db.query(models.InventoryItem).filter(models.InventoryItem.company_id == company.id).all()


@app.post("/inventory/movements", response_model=schemas.StockMovementOut)
async def create_movement(
    movement: schemas.StockMovementCreate,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_api_key)
):
    employee = auth.verify_employee(company, movement.employee_username, movement.employee_password, db)

    item = db.query(models.InventoryItem).filter(
        models.InventoryItem.id == movement.inventory_item_id, models.InventoryItem.company_id == company.id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    if movement.direction not in ("in", "out"):
        raise HTTPException(status_code=400, detail="direction must be 'in' or 'out'")
    if movement.direction == "out" and item.quantity_on_hand < movement.quantity:
        raise HTTPException(status_code=400, detail="Not enough stock on hand")

    item.quantity_on_hand += movement.quantity if movement.direction == "in" else -movement.quantity
    db.add(item)

    linked_tx_id = None
    if movement.record_as_transaction and movement.transaction_amount is not None:
        tx = models.Transaction(
            company_id=company.id, employee_id=employee.username,
            type="income" if movement.direction == "out" else "expense",
            amount=movement.transaction_amount, category=movement.transaction_category or "inventory",
            note=f"Auto-logged from stock movement ({movement.reason or movement.direction})",
            created_at=datetime.utcnow(), inventory_item_id=item.id,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        linked_tx_id = tx.id

        await manager.broadcast_to_company(company.id, {
            "kind": "transaction",
            "id": tx.id, "employee_id": tx.employee_id, "type": tx.type,
            "amount": tx.amount, "category": tx.category, "note": tx.note,
            "created_at": tx.created_at, "synced_from_offline": False,
        })

    db_movement = models.StockMovement(
        company_id=company.id, inventory_item_id=item.id, employee_id=employee.username,
        direction=movement.direction, quantity=movement.quantity, reason=movement.reason,
        linked_transaction_id=linked_tx_id, resulting_quantity_on_hand=item.quantity_on_hand,
    )
    db.add(db_movement)
    db.commit()
    db.refresh(db_movement)

    await manager.broadcast_to_company(company.id, {
        "kind": "stock_movement",
        "inventory_item_id": item.id, "item_name": item.name, "sku": item.sku,
        "direction": movement.direction, "quantity": movement.quantity,
        "quantity_on_hand": item.quantity_on_hand, "reorder_level": item.reorder_level,
        "reason": movement.reason,
    })
    return db_movement


@app.get("/inventory/movements")
def list_movements(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    movements = db.query(models.StockMovement).filter(
        models.StockMovement.company_id == company.id
    ).order_by(models.StockMovement.created_at.desc()).limit(200).all()

    result = []
    for m in movements:
        item = db.query(models.InventoryItem).filter(models.InventoryItem.id == m.inventory_item_id).first()
        result.append({
            "id": m.id,
            "item_name": item.name if item else "(deleted item)",
            "sku": item.sku if item else "",
            "employee_id": m.employee_id,
            "direction": m.direction,
            "quantity": m.quantity,
            "quantity_on_hand": m.resulting_quantity_on_hand,
            "reason": m.reason,
            "created_at": m.created_at,
        })
    return result


@app.post("/inventory/requisitions", response_model=schemas.RequisitionOut)
async def create_requisition(
    payload: schemas.RequisitionCreate,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_api_key)
):
    employee = auth.verify_employee(company, payload.employee_username, payload.employee_password, db)

    item = db.query(models.InventoryItem).filter(
        models.InventoryItem.id == payload.inventory_item_id, models.InventoryItem.company_id == company.id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    if payload.quantity_requested <= 0:
        raise HTTPException(status_code=400, detail="Requested quantity must be greater than zero")

    # Issue whatever is actually available, even if less than requested — the requisition
    # record keeps both numbers so the shortfall is visible, not just silently capped.
    quantity_issued = min(payload.quantity_requested, item.quantity_on_hand)
    if quantity_issued <= 0:
        raise HTTPException(status_code=400, detail="No stock available to issue for this item")

    item.quantity_on_hand -= quantity_issued
    db.add(item)

    requisition = models.Requisition(
        company_id=company.id, inventory_item_id=item.id, employee_id=employee.username,
        product_reference=payload.product_reference, quantity_requested=payload.quantity_requested,
        quantity_issued=quantity_issued, status="open",
    )
    db.add(requisition)

    movement = models.StockMovement(
        company_id=company.id, inventory_item_id=item.id, employee_id=employee.username,
        direction="out", quantity=quantity_issued,
        reason=f"Requisition issued" + (f" — {payload.product_reference}" if payload.product_reference else ""),
        resulting_quantity_on_hand=item.quantity_on_hand,
    )
    db.add(movement)
    db.commit()
    db.refresh(requisition)

    await manager.broadcast_to_company(company.id, {
        "kind": "stock_movement",
        "inventory_item_id": item.id, "item_name": item.name, "sku": item.sku,
        "direction": "out", "quantity": quantity_issued,
        "quantity_on_hand": item.quantity_on_hand, "reorder_level": item.reorder_level,
        "reason": movement.reason,
    })
    await manager.broadcast_to_company(company.id, {"kind": "requisitions_changed"})

    return schemas.RequisitionOut(
        id=requisition.id, inventory_item_id=item.id, item_name=item.name, sku=item.sku,
        employee_id=requisition.employee_id, product_reference=requisition.product_reference,
        quantity_requested=requisition.quantity_requested, quantity_issued=requisition.quantity_issued,
        quantity_consumed=None, quantity_returned=None, wastage=None, status=requisition.status,
        created_at=requisition.created_at, closed_at=None, closed_by=None,
    )


@app.put("/inventory/requisitions/{req_id}/close", response_model=schemas.RequisitionOut)
async def close_requisition(
    req_id: int,
    payload: schemas.RequisitionClose,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_api_key)
):
    employee = auth.verify_employee(company, payload.employee_username, payload.employee_password, db)

    requisition = db.query(models.Requisition).filter(
        models.Requisition.id == req_id, models.Requisition.company_id == company.id
    ).first()
    if not requisition:
        raise HTTPException(status_code=404, detail="Requisition not found")
    if requisition.status == "closed":
        raise HTTPException(status_code=400, detail="This requisition is already closed")

    consumed = payload.quantity_consumed
    returned = payload.quantity_returned
    if consumed < 0 or returned < 0:
        raise HTTPException(status_code=400, detail="Consumed and returned quantities cannot be negative")
    if consumed + returned > requisition.quantity_issued:
        raise HTTPException(
            status_code=400,
            detail=f"Consumed + returned ({consumed + returned}) cannot exceed the {requisition.quantity_issued} issued"
        )

    wastage = requisition.quantity_issued - consumed - returned

    requisition.quantity_consumed = consumed
    requisition.quantity_returned = returned
    requisition.wastage = wastage
    requisition.status = "closed"
    requisition.closed_at = datetime.utcnow()
    requisition.closed_by = employee.username
    db.add(requisition)

    item = db.query(models.InventoryItem).filter(models.InventoryItem.id == requisition.inventory_item_id).first()
    if returned > 0 and item:
        item.quantity_on_hand += returned
        db.add(item)
        movement = models.StockMovement(
            company_id=company.id, inventory_item_id=item.id, employee_id=employee.username,
            direction="in", quantity=returned,
            reason=f"Unused material returned from requisition #{requisition.id}",
            resulting_quantity_on_hand=item.quantity_on_hand,
        )
        db.add(movement)

    db.commit()
    db.refresh(requisition)

    await manager.broadcast_to_company(company.id, {"kind": "requisitions_changed"})
    if returned > 0 and item:
        await manager.broadcast_to_company(company.id, {
            "kind": "stock_movement",
            "inventory_item_id": item.id, "item_name": item.name, "sku": item.sku,
            "direction": "in", "quantity": returned,
            "quantity_on_hand": item.quantity_on_hand, "reorder_level": item.reorder_level,
            "reason": f"Unused material returned from requisition #{requisition.id}",
        })

    return schemas.RequisitionOut(
        id=requisition.id, inventory_item_id=requisition.inventory_item_id,
        item_name=item.name if item else None, sku=item.sku if item else None,
        employee_id=requisition.employee_id, product_reference=requisition.product_reference,
        quantity_requested=requisition.quantity_requested, quantity_issued=requisition.quantity_issued,
        quantity_consumed=requisition.quantity_consumed, quantity_returned=requisition.quantity_returned,
        wastage=requisition.wastage, status=requisition.status,
        created_at=requisition.created_at, closed_at=requisition.closed_at, closed_by=requisition.closed_by,
    )


@app.put("/inventory/requisitions/{req_id}", response_model=schemas.RequisitionOut)
async def correct_requisition(
    req_id: int,
    payload: schemas.RequisitionUpdate,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    """Owner-only correction of a mistaken entry — e.g. wrong quantity_consumed/returned typed
    when closing. If the returned amount changes, the store's stock is adjusted by the
    difference so the correction stays honest, with a movement logged explaining why."""
    requisition = db.query(models.Requisition).filter(
        models.Requisition.id == req_id, models.Requisition.company_id == company.id
    ).first()
    if not requisition:
        raise HTTPException(status_code=404, detail="Requisition not found")

    item = db.query(models.InventoryItem).filter(models.InventoryItem.id == requisition.inventory_item_id).first()

    if payload.product_reference is not None:
        requisition.product_reference = payload.product_reference

    old_returned = requisition.quantity_returned or 0
    new_consumed = payload.quantity_consumed if payload.quantity_consumed is not None else requisition.quantity_consumed
    new_returned = payload.quantity_returned if payload.quantity_returned is not None else requisition.quantity_returned

    if new_consumed is not None and new_returned is not None:
        if new_consumed < 0 or new_returned < 0:
            raise HTTPException(status_code=400, detail="Consumed and returned quantities cannot be negative")
        if new_consumed + new_returned > requisition.quantity_issued:
            raise HTTPException(
                status_code=400,
                detail=f"Consumed + returned ({new_consumed + new_returned}) cannot exceed the {requisition.quantity_issued} issued"
            )

        delta_returned = new_returned - old_returned
        if delta_returned != 0 and item:
            item.quantity_on_hand += delta_returned
            db.add(item)
            movement = models.StockMovement(
                company_id=company.id, inventory_item_id=item.id, employee_id=company.dashboard_username,
                direction="in" if delta_returned > 0 else "out", quantity=abs(delta_returned),
                reason=f"Correction to requisition #{requisition.id} (returned amount adjusted)",
                resulting_quantity_on_hand=item.quantity_on_hand,
            )
            db.add(movement)

        requisition.quantity_consumed = new_consumed
        requisition.quantity_returned = new_returned
        requisition.wastage = requisition.quantity_issued - new_consumed - new_returned

    db.add(requisition)
    db.commit()
    db.refresh(requisition)

    await manager.broadcast_to_company(company.id, {"kind": "requisitions_changed"})
    if item:
        await manager.broadcast_to_company(company.id, {
            "kind": "stock_movement",
            "inventory_item_id": item.id, "item_name": item.name, "sku": item.sku,
            "direction": "in", "quantity": 0,
            "quantity_on_hand": item.quantity_on_hand, "reorder_level": item.reorder_level,
            "reason": f"Correction to requisition #{requisition.id}",
        })

    return schemas.RequisitionOut(
        id=requisition.id, inventory_item_id=requisition.inventory_item_id,
        item_name=item.name if item else None, sku=item.sku if item else None,
        employee_id=requisition.employee_id, product_reference=requisition.product_reference,
        quantity_requested=requisition.quantity_requested, quantity_issued=requisition.quantity_issued,
        quantity_consumed=requisition.quantity_consumed, quantity_returned=requisition.quantity_returned,
        wastage=requisition.wastage, status=requisition.status,
        created_at=requisition.created_at, closed_at=requisition.closed_at, closed_by=requisition.closed_by,
    )


@app.delete("/inventory/requisitions/{req_id}")
async def delete_requisition(
    req_id: int,
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    """Owner-only. Fully reverses the requisition's effect on stock — whatever material
    hasn't already been accounted for as returned goes back into the store — then removes
    the requisition record. The reversal itself stays visible in Stock Movement history."""
    requisition = db.query(models.Requisition).filter(
        models.Requisition.id == req_id, models.Requisition.company_id == company.id
    ).first()
    if not requisition:
        raise HTTPException(status_code=404, detail="Requisition not found")

    item = db.query(models.InventoryItem).filter(models.InventoryItem.id == requisition.inventory_item_id).first()
    already_returned = requisition.quantity_returned or 0
    net_to_reverse = requisition.quantity_issued - already_returned

    if item and net_to_reverse != 0:
        item.quantity_on_hand += net_to_reverse
        db.add(item)
        movement = models.StockMovement(
            company_id=company.id, inventory_item_id=item.id, employee_id=company.dashboard_username,
            direction="in", quantity=net_to_reverse,
            reason=f"Requisition #{requisition.id} deleted — stock reversed",
            resulting_quantity_on_hand=item.quantity_on_hand,
        )
        db.add(movement)

    db.delete(requisition)
    db.commit()

    await manager.broadcast_to_company(company.id, {"kind": "requisitions_changed"})
    if item:
        await manager.broadcast_to_company(company.id, {
            "kind": "stock_movement",
            "inventory_item_id": item.id, "item_name": item.name, "sku": item.sku,
            "direction": "in", "quantity": net_to_reverse,
            "quantity_on_hand": item.quantity_on_hand, "reorder_level": item.reorder_level,
            "reason": f"Requisition #{req_id} deleted — stock reversed",
        })

    return {"status": "deleted", "stock_reversed": net_to_reverse}


@app.get("/inventory/requisitions/open/lookup", response_model=List[schemas.RequisitionOut])
def lookup_open_requisitions(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_api_key)
):
    """Lightweight list of open requisitions for the employee app (to pick one to close)."""
    reqs = db.query(models.Requisition).filter(
        models.Requisition.company_id == company.id, models.Requisition.status == "open"
    ).order_by(models.Requisition.created_at.desc()).all()

    result = []
    for r in reqs:
        item = db.query(models.InventoryItem).filter(models.InventoryItem.id == r.inventory_item_id).first()
        result.append(schemas.RequisitionOut(
            id=r.id, inventory_item_id=r.inventory_item_id,
            item_name=item.name if item else None, sku=item.sku if item else None,
            employee_id=r.employee_id, product_reference=r.product_reference,
            quantity_requested=r.quantity_requested, quantity_issued=r.quantity_issued,
            quantity_consumed=None, quantity_returned=None, wastage=None, status=r.status,
            created_at=r.created_at, closed_at=None, closed_by=None,
        ))
    return result


@app.get("/inventory/requisitions", response_model=List[schemas.RequisitionOut])
def list_requisitions(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    """Full requisition history for the dashboard, including closed ones with wastage."""
    reqs = db.query(models.Requisition).filter(
        models.Requisition.company_id == company.id
    ).order_by(models.Requisition.created_at.desc()).limit(200).all()

    result = []
    for r in reqs:
        item = db.query(models.InventoryItem).filter(models.InventoryItem.id == r.inventory_item_id).first()
        result.append(schemas.RequisitionOut(
            id=r.id, inventory_item_id=r.inventory_item_id,
            item_name=item.name if item else "(deleted item)", sku=item.sku if item else "",
            employee_id=r.employee_id, product_reference=r.product_reference,
            quantity_requested=r.quantity_requested, quantity_issued=r.quantity_issued,
            quantity_consumed=r.quantity_consumed, quantity_returned=r.quantity_returned,
            wastage=r.wastage, status=r.status,
            created_at=r.created_at, closed_at=r.closed_at, closed_by=r.closed_by,
        ))
    return result


@app.get("/inventory/low-stock", response_model=List[schemas.InventoryItemOut])
def low_stock(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    return db.query(models.InventoryItem).filter(
        models.InventoryItem.company_id == company.id,
        models.InventoryItem.quantity_on_hand <= models.InventoryItem.reorder_level
    ).all()


# ============ WEBSOCKET ============

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, user: Optional[str] = Query(None), pw: Optional[str] = Query(None)):
    from database import SessionLocal
    db = SessionLocal()
    company = db.query(models.Company).filter(models.Company.dashboard_username == user).first()
    db.close()

    if not company or not pw or not auth.verify_password(pw, company.dashboard_password_hash):
        await websocket.close(code=4401)
        return

    await manager.connect(websocket, company.id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
