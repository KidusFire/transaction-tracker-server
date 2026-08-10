from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime
import json

from database import engine, get_db
import models
import schemas
import auth

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Company Transaction & Inventory Tracker")
app.mount("/static", StaticFiles(directory="static"), name="static")


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
    )
    db.add(company)
    db.commit()
    db.refresh(company)

    return schemas.CompanySignupOut(
        company_id=company.id, company_name=company.name, api_key=company.api_key,
        dashboard_username=company.dashboard_username, plan=company.plan,
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
    )
    db.add(db_tx)
    db.commit()
    db.refresh(db_tx)

    await manager.broadcast_to_company(company.id, {
        "kind": "transaction",
        "id": db_tx.id, "employee_id": db_tx.employee_id, "type": db_tx.type,
        "amount": db_tx.amount, "category": db_tx.category, "note": db_tx.note,
        "created_at": db_tx.created_at, "synced_from_offline": db_tx.synced_from_offline,
    })
    return db_tx


@app.get("/transactions", response_model=List[schemas.TransactionOut])
def list_transactions(
    db: Session = Depends(get_db),
    company: models.Company = Depends(auth.get_company_from_dashboard_login)
):
    return db.query(models.Transaction).filter(models.Transaction.company_id == company.id)\
        .order_by(models.Transaction.created_at.desc()).all()


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
        linked_transaction_id=linked_tx_id,
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
