from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


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
    recovery_key: str


class CompanyOut(BaseModel):
    id: int
    name: str
    plan: str
    currency: str
    trial_ends_at: Optional[datetime] = None
    bank_details: Optional[str] = None
    logo_image: Optional[str] = None
    logo_mime: Optional[str] = None

    class Config:
        from_attributes = True


class PasswordResetRequest(BaseModel):
    dashboard_username: str
    recovery_key: str
    new_password: str


class RecoveryKeyOut(BaseModel):
    recovery_key: str


class BillingCheckoutRequest(BaseModel):
    plan: str
    customer_email: str


class BillingCheckoutResponse(BaseModel):
    checkout_url: str


class LineItemCreate(BaseModel):
    description: str
    quantity: float
    unit_price: float


class SalesOrderCreate(BaseModel):
    employee_username: str
    employee_password: str
    customer_name: str
    customer_contact: Optional[str] = None
    currency: str = "USD"
    note: Optional[str] = None
    validity_days: int = 30
    delivery_terms: Optional[str] = None
    downpayment_percent: Optional[float] = None
    payment_terms: Optional[str] = None
    vat_percent: float = 15.0
    line_items: List[LineItemCreate]


class LineItemOut(BaseModel):
    id: int
    description: str
    quantity: float
    unit_price: float

    class Config:
        from_attributes = True


class SalesOrderOut(BaseModel):
    id: int
    employee_id: str
    customer_name: str
    customer_contact: Optional[str]
    currency: str
    status: str
    note: Optional[str]
    validity_days: int
    delivery_terms: Optional[str]
    downpayment_percent: Optional[float]
    payment_terms: Optional[str]
    vat_percent: float = 15.0
    created_at: datetime
    updated_at: datetime
    line_items: List[LineItemOut] = []

    class Config:
        from_attributes = True


class CompanyBankDetailsUpdate(BaseModel):
    bank_details: str


class CompanyLogoUpdate(BaseModel):
    logo_image: str    # base64-encoded
    logo_mime: str      # e.g. "image/png"


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


class RequisitionUpdate(BaseModel):
    """Owner-only correction of a requisition already submitted (open or closed)."""
    product_reference: Optional[str] = None
    quantity_consumed: Optional[float] = None
    quantity_returned: Optional[float] = None


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
