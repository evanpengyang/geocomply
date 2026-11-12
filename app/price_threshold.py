"""One-variable price boundary for the existing pair model; no routing action."""
from decimal import Decimal


def price_threshold(name_a: str, name_b: str, matches_a: int, matches_b: int,
                    price_a: Decimal, price_b: Decimal) -> dict:
    """Hold A's price and both exact match counts fixed, vary B's price.

    Saving from A→B to B→A = matches_b*price_a - matches_a*price_b.
    Never divide rounded display rates or use rounded prices for the decision.
    """
    assumption = (f"Assumes {name_a} stays at ${price_a:.4f} per check and the observed "
                  "calibration match rates remain unchanged. Existing pair-model assumptions apply.")
    result = {"classification": "Modeled", "vendor_name": name_b,
              "current_price_usd": str(price_b), "threshold_usd": None,
              "assumption": assumption, "formula": "Threshold for B = matches_B × price_A ÷ matches_A"}
    if matches_a == 0:
        detail = (f"{name_b} first remains cheaper at any nonnegative price in this model because "
                  f"{name_a} has no calibration matches." if matches_b * price_a > 0 else
                  "The orders have equal modeled cost at every nonnegative price for this vendor.")
        return {**result, "status": "no_finite_threshold", "headline": "No finite price threshold", "detail": detail}
    boundary = Decimal(matches_b) * price_a / Decimal(matches_a)
    saving = Decimal(matches_b) * price_a - Decimal(matches_a) * price_b
    result.update(threshold_usd=str(boundary), status="available")
    if boundary == 0:
        detail = (f"No nonnegative price makes {name_b} first strictly cheaper under this model. "
                  "At $0 per check the orders tie.")
    else:
        displayed = f"approximately ${boundary:.3f}" if boundary >= Decimal("0.0005") else f"approximately ${boundary:.8f}"
        detail = (f"{name_b} currently costs ${price_b:.4f} per check. Putting it first "
                  f"{'remains' if saving > 0 else 'becomes'} cheaper below {displayed} per check. "
                  "At the exact threshold the orders tie; above it the current order is cheaper.")
    return {**result, "headline": "When would this recommendation change?" if saving > 0 else
            "What would make this swap worthwhile?", "detail": detail}
