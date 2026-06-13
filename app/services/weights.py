def parse_manual_weights(form) -> dict:
    tw = form.get("tw", type=float)
    return {
        "weight_per_quantity": form.get("weight_per_quantity", type=float),
        "gross_weight": form.get("gross_weight", type=float),
        "tw": tw if tw is not None else 0,
        "net_weight": form.get("net_weight", type=float),
    }
