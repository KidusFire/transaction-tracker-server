import sys
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QListWidget, QMessageBox, QTabWidget, QCheckBox
)

import local_db
import sync
import config as cfg
import inventory_api


def run_first_time_setup():
    """Shown once per PC: the employee enters the login their manager created for them
    on the dashboard, plus the company API key (given by the manager during rollout)."""
    app = QApplication.instance() or QApplication(sys.argv)
    settings = cfg.load_config()

    dialog = QWidget()
    dialog.setWindowTitle("First-time setup")
    layout = QVBoxLayout()

    layout.addWidget(QLabel("Company API key (ask your manager):"))
    api_key_input = QLineEdit()
    api_key_input.setText(settings.get("api_key", ""))
    layout.addWidget(api_key_input)

    layout.addWidget(QLabel("Server URL:"))
    server_input = QLineEdit()
    server_input.setText(settings.get("server_url", ""))
    layout.addWidget(server_input)

    layout.addWidget(QLabel("Your employee username (ask your manager):"))
    username_input = QLineEdit()
    layout.addWidget(username_input)

    layout.addWidget(QLabel("Your employee password:"))
    password_input = QLineEdit()
    password_input.setEchoMode(QLineEdit.Password)
    layout.addWidget(password_input)

    save_btn = QPushButton("Save and continue")
    layout.addWidget(save_btn)
    dialog.setLayout(layout)

    result = {"done": False}

    def on_save():
        if username_input.text().strip() and password_input.text():
            settings["api_key"] = api_key_input.text().strip()
            settings["server_url"] = server_input.text().strip()
            settings["employee_username"] = username_input.text().strip()
            settings["employee_password"] = password_input.text()
            cfg.save_config(settings)
            result["done"] = True
            dialog.close()

    save_btn.clicked.connect(on_save)
    dialog.show()
    while not result["done"]:
        app.processEvents()

    return settings


class TransactionTab(QWidget):
    def __init__(self):
        super().__init__()
        self.build_ui()
        self.refresh_list()

    def build_ui(self):
        layout = QVBoxLayout()

        self.type_dropdown = QComboBox()
        self.type_dropdown.addItems(["income", "expense"])
        layout.addWidget(QLabel("Type"))
        layout.addWidget(self.type_dropdown)

        self.amount_input = QLineEdit()
        self.amount_input.setPlaceholderText("e.g. 1500")
        layout.addWidget(QLabel("Amount"))
        layout.addWidget(self.amount_input)

        self.category_input = QLineEdit()
        self.category_input.setPlaceholderText("e.g. sales, rent, supplies")
        layout.addWidget(QLabel("Category"))
        layout.addWidget(self.category_input)

        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("optional note")
        layout.addWidget(QLabel("Note"))
        layout.addWidget(self.note_input)

        submit_btn = QPushButton("Save Transaction")
        submit_btn.clicked.connect(self.submit_transaction)
        layout.addWidget(submit_btn)

        layout.addWidget(QLabel("Today's entries:"))
        self.history_list = QListWidget()
        layout.addWidget(self.history_list)

        self.setLayout(layout)

    def submit_transaction(self):
        amount_text = self.amount_input.text().strip()
        category = self.category_input.text().strip()

        if not amount_text or not category:
            QMessageBox.warning(self, "Missing info", "Amount and category are required.")
            return
        try:
            amount = float(amount_text)
        except ValueError:
            QMessageBox.warning(self, "Invalid amount", "Amount must be a number.")
            return

        local_db.save_transaction(
            type_=self.type_dropdown.currentText(),
            amount=amount,
            category=category,
            note=self.note_input.text().strip() or None,
        )

        self.amount_input.clear()
        self.category_input.clear()
        self.note_input.clear()
        self.refresh_list()

    def refresh_list(self):
        self.history_list.clear()
        for row in local_db.get_today_transactions():
            status = "✓ synced" if row["synced"] else "… pending sync"
            self.history_list.addItem(
                f"{row['type'].upper()} - {row['amount']} ({row['category']}) [{status}]"
            )


class InventoryTab(QWidget):
    """
    Note: unlike transactions, inventory actions here require an internet connection
    (they call the server directly rather than queueing offline).
    """
    def __init__(self):
        super().__init__()
        self.items = []
        self.build_ui()
        self.load_items()

    def build_ui(self):
        layout = QVBoxLayout()

        layout.addWidget(QLabel("— Add New Item —"))

        self.new_sku_input = QLineEdit()
        self.new_sku_input.setPlaceholderText("e.g. RM-001")
        layout.addWidget(QLabel("SKU"))
        layout.addWidget(self.new_sku_input)

        self.new_name_input = QLineEdit()
        self.new_name_input.setPlaceholderText("e.g. Raw cotton fabric")
        layout.addWidget(QLabel("Name"))
        layout.addWidget(self.new_name_input)

        self.new_unit_dropdown = QComboBox()
        self.new_unit_dropdown.addItems(["pcs", "kg", "g", "liters", "boxes", "meters", "units"])
        self.new_unit_dropdown.setEditable(True)
        layout.addWidget(QLabel("Unit"))
        layout.addWidget(self.new_unit_dropdown)

        self.new_qty_input = QLineEdit()
        self.new_qty_input.setPlaceholderText("starting quantity (default 0)")
        layout.addWidget(QLabel("Starting Quantity"))
        layout.addWidget(self.new_qty_input)

        self.new_reorder_input = QLineEdit()
        self.new_reorder_input.setPlaceholderText("alert when stock falls to/below this (default 0)")
        layout.addWidget(QLabel("Reorder Level"))
        layout.addWidget(self.new_reorder_input)

        self.new_cost_input = QLineEdit()
        self.new_cost_input.setPlaceholderText("cost per unit (default 0)")
        layout.addWidget(QLabel("Unit Cost"))
        layout.addWidget(self.new_cost_input)

        add_item_btn = QPushButton("Add Item")
        add_item_btn.clicked.connect(self.submit_new_item)
        layout.addWidget(add_item_btn)

        layout.addWidget(QLabel("— Log Stock Movement —"))

        self.item_dropdown = QComboBox()
        layout.addWidget(QLabel("Item"))
        layout.addWidget(self.item_dropdown)

        refresh_btn = QPushButton("Refresh item list")
        refresh_btn.clicked.connect(self.load_items)
        layout.addWidget(refresh_btn)

        self.direction_dropdown = QComboBox()
        self.direction_dropdown.addItems(["out (sale/usage)", "in (restock/production)"])
        layout.addWidget(QLabel("Direction"))
        layout.addWidget(self.direction_dropdown)

        self.qty_input = QLineEdit()
        self.qty_input.setPlaceholderText("e.g. 10")
        layout.addWidget(QLabel("Quantity"))
        layout.addWidget(self.qty_input)

        self.reason_input = QLineEdit()
        self.reason_input.setPlaceholderText("e.g. walk-in sale, weekly restock")
        layout.addWidget(QLabel("Reason"))
        layout.addWidget(self.reason_input)

        self.also_log_money = QCheckBox("Also log this as a money transaction")
        layout.addWidget(self.also_log_money)

        self.money_amount_input = QLineEdit()
        self.money_amount_input.setPlaceholderText("amount (if logging as transaction)")
        layout.addWidget(self.money_amount_input)

        submit_btn = QPushButton("Log Movement")
        submit_btn.clicked.connect(self.submit_movement)
        layout.addWidget(submit_btn)

        layout.addWidget(QLabel("Available items (refresh to update):"))
        self.item_list = QListWidget()
        layout.addWidget(self.item_list)

        self.setLayout(layout)

    def submit_new_item(self):
        sku = self.new_sku_input.text().strip()
        name = self.new_name_input.text().strip()
        unit = self.new_unit_dropdown.currentText().strip() or "pcs"

        if not sku or not name:
            QMessageBox.warning(self, "Missing info", "SKU and name are required.")
            return

        def parse_or_default(text, default=0.0):
            text = text.strip()
            if not text:
                return default
            try:
                return float(text)
            except ValueError:
                return None

        qty = parse_or_default(self.new_qty_input.text())
        reorder = parse_or_default(self.new_reorder_input.text())
        cost = parse_or_default(self.new_cost_input.text())

        if qty is None or reorder is None or cost is None:
            QMessageBox.warning(self, "Invalid number", "Quantity, reorder level, and unit cost must be numbers.")
            return

        try:
            inventory_api.create_item(sku, name, unit, qty, reorder, cost)
            self.new_sku_input.clear()
            self.new_name_input.clear()
            self.new_qty_input.clear()
            self.new_reorder_input.clear()
            self.new_cost_input.clear()
            self.load_items()
            QMessageBox.information(self, "Item added", f"'{name}' added to inventory.")
        except Exception as e:
            QMessageBox.warning(self, "Failed to add item", str(e))

    def load_items(self):
        try:
            self.items = inventory_api.list_items()
            self.item_dropdown.clear()
            self.item_list.clear()
            for item in self.items:
                label = f"{item['sku']} — {item['name']} ({item['quantity_on_hand']} {item['unit']})"
                self.item_dropdown.addItem(label, userData=item["id"])
                self.item_list.addItem(label)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't load items", f"Check your internet connection.\n{e}")

    def submit_movement(self):
        if self.item_dropdown.currentIndex() < 0:
            QMessageBox.warning(self, "No item", "Load and select an item first.")
            return

        qty_text = self.qty_input.text().strip()
        try:
            qty = float(qty_text)
        except ValueError:
            QMessageBox.warning(self, "Invalid quantity", "Quantity must be a number.")
            return

        direction = "out" if self.direction_dropdown.currentIndex() == 0 else "in"
        item_id = self.item_dropdown.currentData()

        record_as_tx = self.also_log_money.isChecked()
        tx_amount = None
        if record_as_tx:
            try:
                tx_amount = float(self.money_amount_input.text().strip())
            except ValueError:
                QMessageBox.warning(self, "Invalid amount", "Enter a valid transaction amount.")
                return

        try:
            inventory_api.create_movement(
                inventory_item_id=item_id,
                direction=direction,
                quantity=qty,
                reason=self.reason_input.text().strip() or None,
                record_as_transaction=record_as_tx,
                transaction_amount=tx_amount,
                transaction_category="inventory",
            )
            self.qty_input.clear()
            self.reason_input.clear()
            self.money_amount_input.clear()
            self.also_log_money.setChecked(False)
            self.load_items()
        except Exception as e:
            QMessageBox.warning(self, "Failed to log movement", str(e))


class HistoryTab(QWidget):
    """Lets the employee see their own past transactions, fetched from the server."""
    def __init__(self):
        super().__init__()
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout()

        refresh_btn = QPushButton("Refresh my history")
        refresh_btn.clicked.connect(self.load_history)
        layout.addWidget(refresh_btn)

        self.history_list = QListWidget()
        layout.addWidget(self.history_list)

        self.setLayout(layout)
        self.load_history()

    def load_history(self):
        try:
            history = inventory_api.fetch_my_history()
            self.history_list.clear()
            if not history:
                self.history_list.addItem("No transactions logged yet.")
                return
            for tx in history:
                label = f"{tx['created_at'][:16].replace('T', ' ')} — {tx['type'].upper()} {tx['amount']} ({tx['category']})"
                if tx.get("note"):
                    label += f" — {tx['note']}"
                self.history_list.addItem(label)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't load history", f"Check your internet connection.\n{e}")


class MainWindow(QWidget):
    def __init__(self, employee_username):
        super().__init__()
        self.setWindowTitle(f"Company Tracker — {employee_username}")
        self.setMinimumWidth(450)
        self.setMinimumHeight(500)

        layout = QVBoxLayout()
        tabs = QTabWidget()
        tabs.addTab(TransactionTab(), "Transactions")
        tabs.addTab(InventoryTab(), "Inventory")
        tabs.addTab(HistoryTab(), "History")
        layout.addWidget(tabs)
        self.setLayout(layout)


if __name__ == "__main__":
    local_db.init_db()

    settings = cfg.load_config()
    if cfg.needs_setup(settings):
        settings = run_first_time_setup()

    sync.start_background_sync()

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(settings["employee_username"])
    window.show()
    sys.exit(app.exec())
