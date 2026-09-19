def apply_discount(price: float, percent: float) -> float:
    """Return the price after applying a percentage discount."""
    return price - (price * percent)
