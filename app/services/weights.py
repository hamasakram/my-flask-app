def calculate_gross_net(quantity: float, weight_per_quantity: float, tw: float = 0):
    gross_weight = quantity * weight_per_quantity
    net_weight = gross_weight - (tw or 0)
    return gross_weight, net_weight
