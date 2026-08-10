from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    api_key = Column(String, unique=True, index=True)
    dashboard_username = Column(String, unique=True, index=True)
    dashboard_password_hash = Column(String)
    plan = Column(String, default="free")          # "free", "starter", "growth", "enterprise"
    is_active = Column(Boolean, default=True)       # for suspending a delinquent/cancelled account later
    created_at = Column(DateTime, default=datetime.utcnow)

    transactions = relationship("Transaction", back_populates="company")
    inventory_items = relationship("InventoryItem", back_populates="company")
    employees = relationship("Employee", back_populates="company")


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), index=True)
    username = Column(String, index=True)     # unique per company, not globally
    password_hash = Column(String)
    full_name = Column(String, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="employees")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), index=True)
    employee_id = Column(String, index=True)   # stores the employee's username, set from verified login
    type = Column(String)
    amount = Column(Float)
    category = Column(String)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    synced_from_offline = Column(Boolean, default=False)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=True)

    company = relationship("Company", back_populates="transactions")


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), index=True)
    sku = Column(String, index=True)
    name = Column(String)
    unit = Column(String, default="pcs")
    quantity_on_hand = Column(Float, default=0)
    reorder_level = Column(Float, default=0)
    unit_cost = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="inventory_items")


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), index=True)
    inventory_item_id = Column(Integer, ForeignKey("inventory_items.id"), index=True)
    employee_id = Column(String)
    direction = Column(String)
    quantity = Column(Float)
    reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    linked_transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
