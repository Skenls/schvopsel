CREW = ("Блят", "Внуч", "Птич", "Чун", "Шланг", "Шлюп", "Мышь")
MAIN_ADMIN_MEMBER = CREW.index("Мышь")
ROOMS = ("deck", "captain", "gym", "hold", "computers", "bar", "hammam")


def initial_vessel(name: str) -> str:
    return "dinghy" if name in ("Шлюп", "Чун") else "ship"


def rooms_for(vessel: str) -> tuple[str, ...]:
    return ("deck",) if vessel == "dinghy" else ROOMS
