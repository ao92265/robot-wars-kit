LOADOUT = {"hp": 3, "speed": 3, "damage": 4, "range": 2, "special": 0}
def decide(view):
    if not view.enemies: return {"turn": 10}
    t = view.enemies[0]; a = {"turn": t.bearing, "thrust": "forward"}
    if abs(t.bearing) < view.self.weapon_arc and t.dist <= view.self.weapon_range:
        a["fire"] = True
    return a
