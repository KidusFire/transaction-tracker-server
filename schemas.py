from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class TransactionCreate(BaseModel):
    employee_username: str
    employee_password: str
    type: str          # "income" or "expense"
    amount: float
    category: str
    note: Optional[str] = None
    created_at: Optional[datetime] = None
    synced_from_offline: Optional[bool] = False


class TransactionOut(BaseModel):
    id: int
    employee_id: str
    type: str
    amount: float
    category: str
    note: Optional[str]
    created_at: datetime
    synced_from_offline: bool

    class Config:
        from_attributes = True


class CompanySignup(BaseModel):
    company_name: str
    dashboard_username: str
    dashboard_password: str
    plan: str = "free"


class CompanySignupOut(BaseModel):
    company_id: int
    company_name: str
    api_key: str
    dashboard_username: str
    plan: str


class EmployeeCreate(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = None


class EmployeeOut(BaseModel):
    id: int
    username: str
    full_name: Optional[str]
    active: bool

    class Config:
        from_attributes = True


class InventoryItemCreate(BaseModel):
    sku: str
    name: str
    unit: str = "pcs"
    quantity_on_hand: float = 0
    reorder_level: float = 0
    unit_cost: float = 0


class InventoryItemOut(BaseModel):
    id: int
    sku: str
    name: str
    unit: str
    quantity_on_hand: float
    reorder_level: float
    unit_cost: float

    class Config:
        from_attributes = True


class StockMovementCreate(BaseModel):
    inventory_item_id: int
    employee_username: str
    employee_password: str
    direction: str      # "in" or "out"
    quantity: float
    reason: Optional[str] = None
    # if this movement should also log a money transaction (e.g. a sale):
    record_as_transaction: bool = False
    transaction_amount: Optional[float] = None
    transaction_category: Optional[str] = None


class StockMovementOut(BaseModel):
    id: int
    inventory_item_id: int
    employee_id: str
    direction: str
    quantity: float
    reason: Optional[str]
    created_at: datetime
    linked_transaction_id: Optional[int]

    class Config:
        from_attributes = True
