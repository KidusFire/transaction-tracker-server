import requests
import config as cfg


def _headers():
    settings = cfg.load_config()
    return settings["server_url"], {"X-API-Key": settings["api_key"]}


def fetch_my_history():
    """Returns this employee's own past transactions from the server."""
    server_url, headers = _headers()
    settings = cfg.load_config()
    params = {
        "employee_username": settings["employee_username"],
        "employee_password": settings["employee_password"],
    }
    r = requests.get(f"{server_url}/transactions/mine", headers=headers, params=params, timeout=5)
    r.raise_for_status()
    return r.json()


def list_items():
    """Items visible to the employee app, for picking one when logging a movement."""
    server_url, headers = _headers()
    r = requests.get(f"{server_url}/inventory/items/lookup", headers=headers, timeout=5)
    r.raise_for_status()
    return r.json()


def create_item(sku, name, unit, quantity_on_hand, reorder_level, unit_cost):
    server_url, headers = _headers()
    payload = {
        "sku": sku, "name": name, "unit": unit,
        "quantity_on_hand": quantity_on_hand,
        "reorder_level": reorder_level,
        "unit_cost": unit_cost,
    }
    r = requests.post(f"{server_url}/inventory/items", json=payload, headers=headers, timeout=5)
    r.raise_for_status()
    return r.json()


def create_movement(inventory_item_id, direction, quantity, reason,
                     record_as_transaction=False, transaction_amount=None, transaction_category=None):
    server_url, headers = _headers()
    settings = cfg.load_config()
    payload = {
        "inventory_item_id": inventory_item_id,
        "employee_username": settings["employee_username"],
        "employee_password": settings["employee_password"],
        "direction": direction,
        "quantity": quantity,
        "reason": reason,
        "record_as_transaction": record_as_transaction,
        "transaction_amount": transaction_amount,
        "transaction_category": transaction_category,
    }
    r = requests.post(f"{server_url}/inventory/movements", json=payload, headers=headers, timeout=5)
    r.raise_for_status()
    return r.json()
