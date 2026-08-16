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
    receipt_image: Optional[str] = None   # base64-encoded image
    receipt_mime: Optional[str] = None    # e.g. "image/jpeg"
    currency: Optional[str] = None        # if omitted, falls back to the company's default currency


class TransactionOut(BaseModel):
    id: int
    employee_id: str
    type: str
    amount: float
    category: str
    note: Optional[str]
    created_at: datetime
    synced_from_offline: bool
    receipt_mime: Optional[str] = None   # presence of this tells the dashboard a receipt exists
    currency: str = "USD"

    class Config:
        from_attributes = True


class TransactionUpdate(BaseModel):
    type: Optional[str] = None
    amount: Optional[float] = None
    category: Optional[str] = None
    note: Optional[str] = None
    currency: Optional[str] = None


class CompanySignup(BaseModel):
    company_name: str
    dashboard_username: str
    dashboard_password: str
    plan: str = "free"
    currency: str = "USD"


class CompanySignupOut(BaseModel):
    company_id: int
    company_name: str
    api_key: str
    dashboard_username: str
    plan: str
    currency: str


class CompanyOut(BaseModel):
    id: int
    name: str
    plan: str
    currency: str

    class Config:
        from_attributes = True


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
    category: str = "raw_material"   # "raw_material" or "fixed_asset"
    quantity_on_hand: float = 0
    reorder_level: float = 0
    unit_cost: float = 0
    acquisition_date: Optional[datetime] = None
    location: Optional[str] = None
    serial_number: Optional[str] = None
    condition: Optional[str] = None
    useful_life_years: Optional[float] = None
    salvage_value: Optional[float] = 0


class InventoryItemOut(BaseModel):
    id: int
    sku: str
    name: str
    unit: str
    category: str
    quantity_on_hand: float
    reorder_level: float
    unit_cost: float
    acquisition_date: Optional[datetime] = None
    location: Optional[str] = None
    serial_number: Optional[str] = None
    condition: Optional[str] = None
    useful_life_years: Optional[float] = None
    salvage_value: Optional[float] = None

    class Config:
        from_attributes = True


class InventoryItemUpdate(BaseModel):
    name: Optional[str] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    reorder_level: Optional[float] = None
    unit_cost: Optional[float] = None
    acquisition_date: Optional[datetime] = None
    location: Optional[str] = None
    serial_number: Optional[str] = None
    condition: Optional[str] = None
    useful_life_years: Optional[float] = None
    salvage_value: Optional[float] = None


class RequisitionCreate(BaseModel):
    inventory_item_id: int
    employee_username: str
    employee_password: str
    quantity_requested: float
    product_reference: Optional[str] = None


class RequisitionClose(BaseModel):
    employee_username: str
    employee_password: str
    quantity_consumed: float
    quantity_returned: float = 0


class RequisitionOut(BaseModel):
    id: int
    inventory_item_id: int
    item_name: Optional[str] = None
    sku: Optional[str] = None
    employee_id: str
    product_reference: Optional[str]
    quantity_requested: float
    quantity_issued: float
    quantity_consumed: Optional[float]
    quantity_returned: Optional[float]
    wastage: Optional[float]
    status: str
    created_at: datetime
    closed_at: Optional[datetime]
    closed_by: Optional[str]

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
