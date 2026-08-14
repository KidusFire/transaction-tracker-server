from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from typing import List, Optional
from datetime import datetime
import json

from database import engine, get_db
import models
import schemas
import auth

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

app = FastAPI(title="Company Transaction & Inventory Tracker")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/company/me", response_model=schemas.CompanyOut)
def get_my_company(company: models.Company = Depends(auth.get_company_from_dashboard_login)):
    return company


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(company: models.Company = Depends(auth.get_company_from_dashboard_login)):
    with open("static/dashboard.html", encoding="utf-8") as f:
        return f.read()


@app.get("/signup", response_class=HTMLResponse)
def signup_page():
    with open("static/signup.html", encoding="utf-8") as f:
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

    company = models.Company(
        name=payload.company_name,
        api_key=auth.generate_api_key(),
        dashboard_username=payload.dashboard_username,
        dashboard_password_hash=auth.hash_password(payload.dashboard_password),
        plan=payload.plan,
        currency=payload.currency,
    )
    db.add(company)
    db.commit()
    db.refresh(company)

    return schemas.CompanySignupOut(
        company_id=company.id, company_name=company.name, api_key=company.api_key,
        dashboard_username=company.dashboard_username, plan=company.plan, currency=company.currency,
    )


# ============ EMPLOYEE MANAGEMENT (dashboard/owner only) ============

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
    )
    db.add(db_tx)
    db.commit()
    db.refresh(db_tx)

    await manager.broadcast_to_company(company.id, {
        "kind": "transaction",
        "id": db_tx.id, "employee_id": db_tx.employee_id, "type": db_tx.type,
        "amount": db_tx.amount, "category": db_tx.category, "note": db_tx.note,
        "created_at": db_tx.created_at, "synced_from_offline": db_tx.synced_from_offline,
        "receipt_mime": db_tx.receipt_mime,
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
    """start_date and end_date as YYYY-MM-DD. Returns totals plus a day-by-day breakdown."""
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

    daily = {}
    total_income = 0
    total_expense = 0

    for tx in transactions:
        day = tx.created_at.date().isoformat()
        if day not in daily:
            daily[day] = {"income": 0, "expense": 0}
        daily[day][tx.type] += tx.amount
        if tx.type == "income":
            total_income += tx.amount
        else:
            total_expense += tx.amount

    daily_breakdown = [
        {"date": day, "income": vals["income"], "expense": vals["expense"], "net": vals["income"] - vals["expense"]}
        for day, vals in sorted(daily.items())
    ]

    transaction_list = [
        {
            "id": tx.id, "employee_id": tx.employee_id, "type": tx.type,
            "amount": tx.amount, "category": tx.category, "note": tx.note,
            "created_at": tx.created_at,
        }
        for tx in transactions
    ]

    return {
        "income": total_income,
        "expense": total_expense,
        "net": total_income - total_expense,
        "daily": daily_breakdown,
        "transactions": transaction_list,
    }


@app.get("/summary/today")
def summary_today(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    base = db.query(func.sum(models.Transaction.amount)).filter(
        models.Transaction.company_id == company.id, models.Transaction.created_at >= start_of_day
    )
    income = base.filter(models.Transaction.type == "income").scalar() or 0
    expense = base.filter(models.Transaction.type == "expense").scalar() or 0
    return {"income": income, "expense": expense, "net": income - expense}


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
        "quantity_on_hand": item.quantity_on_hand, "reorder_level": item.reorder_level,
        "unit_cost": item.unit_cost,
    })

    return item


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
