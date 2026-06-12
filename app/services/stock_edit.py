"""Helpers for editing stock records without breaking live inventory math."""


def stock_before_used_transaction(current_stock: float, used_quantity: float) -> float:
    """Stock level immediately before a used transaction (add back what was consumed)."""
    return current_stock + used_quantity


def stock_before_received_transaction(current_stock: float, received_quantity: float) -> float:
    """Stock level immediately before a received transaction (remove what was added)."""
    return current_stock - received_quantity
