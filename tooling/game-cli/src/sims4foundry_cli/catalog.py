"""Typed semantic contract. Catalog membership is NOT a live support claim."""
from dataclasses import dataclass
from .errors import FoundryError
from .io import exact_fields, exact_int, finite_number

STATES = ("closed", "main_menu", "cas", "live", "build")


def identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 128 and all(c.isalnum() or c in "_.:-" for c in value)


def label(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and all(ord(c) >= 32 for c in value)


def choice(*values):
    return lambda value: type(value) is str and value in values


VALIDATORS = {
    "id": identifier,
    "label": label,
    "unit": lambda v: finite_number(v, 0, 1),
    "signed_unit": lambda v: finite_number(v, -1, 1),
    "coordinate": lambda v: finite_number(v, -10000, 10000),
    "angle": lambda v: finite_number(v, -360, 360),
    "floor": lambda v: exact_int(v, -4, 10),
    "speed": lambda v: exact_int(v, 0, 3),
    "index": lambda v: exact_int(v, 0, 1000),
    "positive": lambda v: finite_number(v, 0.01, 1000),
    "boolean": lambda v: type(v) is bool,
    "save_policy": choice("save", "discard"),
    "category": choice("everyday", "formal", "athletic", "sleep", "party", "swim", "hot_weather", "cold_weather"),
    "color": lambda v: isinstance(v, str) and len(v) == 7 and v.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in v[1:]),
}


@dataclass(frozen=True)
class Operation:
    name: str
    states: tuple
    arguments: dict
    effect: str
    destructive: bool = False

    def validate(self, args):
        exact_fields(args, self.arguments)
        for key, kind in self.arguments.items():
            if not VALIDATORS[kind](args[key]):
                raise FoundryError("invalid_argument", "Semantic argument failed its type/range check.", argument=key, expected=kind)

    def public(self, backend):
        return {"operation": self.name, "allowed_states": self.states, "arguments": self.arguments,
                "effect": self.effect, "destructive": self.destructive,
                "availability": "simulation_only" if backend == "simulation" else "unqualified",
                "live_qualified": False,
                "blocker": None if backend == "simulation" else "No independently qualified no-Apex semantic adapter is installed."}


def op(name, states, args=None, effect="edit", destructive=False):
    return Operation(name, tuple(states.split()), args or {}, effect, destructive)


_ITEMS = [
    op("game.launch", "closed", effect="main_menu"),
    op("game.new", "main_menu", effect="cas"),
    op("game.load", "main_menu", {"slot_id": "id"}, "live"),
    op("game.save", "live build", {"slot_id": "id", "slot_name": "label"}, "save", True),
    op("game.main_menu", "cas live build", {"save_policy": "save_policy"}, "main_menu", True),
    op("game.exit", "main_menu cas live build", {"save_policy": "save_policy"}, "closed", True),
    op("cas.enter", "live", {"sim_id": "id"}, "cas"),
    op("cas.accept", "cas", effect="live", destructive=True),
    op("cas.cancel", "cas", effect="live", destructive=True),
    op("cas.select_sim", "cas", {"sim_id": "id"}),
    op("cas.select_form", "cas", {"form_id": "id"}),
    op("cas.select_outfit", "cas", {"category": "category", "index": "index"}),
    op("cas.add_outfit", "cas", {"category": "category"}),
    op("cas.remove_outfit", "cas", {"category": "category", "index": "index"}, destructive=True),
    op("cas.open_panel", "cas", {"panel_id": "id"}),
    op("cas.search", "cas", {"query": "label"}),
    op("cas.apply_part", "cas", {"part_id": "id", "body_type": "id"}),
    op("cas.remove_part", "cas", {"body_type": "id"}, destructive=True),
    op("cas.select_swatch", "cas", {"part_id": "id", "swatch_id": "id"}),
    op("cas.set_modifier", "cas", {"modifier_id": "id", "value": "signed_unit"}),
    op("cas.set_physique", "cas", {"fitness": "unit", "weight": "unit"}),
    op("cas.set_voice", "cas", {"voice_id": "id", "pitch": "unit"}),
    op("cas.set_walkstyle", "cas", {"walkstyle_id": "id"}),
    op("cas.set_name", "cas", {"first_name": "label", "last_name": "label"}),
    op("cas.set_trait", "cas", {"slot": "index", "trait_id": "id"}),
    op("cas.undo", "cas", effect="undo"), op("cas.redo", "cas", effect="redo"),
    op("live.select_sim", "live", {"sim_id": "id"}),
    op("live.set_speed", "live", {"speed": "speed"}),
    op("live.interact", "live", {"actor_id": "id", "target_id": "id", "interaction_id": "id"}),
    op("live.cancel_interaction", "live", {"interaction_id": "id"}, destructive=True),
    op("live.travel", "live", {"lot_id": "id"}),
    op("live.select_household", "live", {"household_id": "id"}),
    op("build.enter", "live", effect="build"), op("build.exit", "build", effect="live"),
    op("build.search", "build", {"query": "label"}),
    op("build.place", "build", {"catalog_id": "id", "x": "coordinate", "y": "coordinate", "floor": "floor", "rotation": "angle"}),
    op("build.move", "build", {"object_id": "id", "x": "coordinate", "y": "coordinate", "floor": "floor", "rotation": "angle"}),
    op("build.delete", "build", {"object_id": "id"}, destructive=True),
    op("build.set_swatch", "build", {"object_id": "id", "swatch_id": "id"}),
    op("build.draw_room", "build", {"x": "coordinate", "y": "coordinate", "width": "positive", "height": "positive", "floor": "floor"}),
    op("build.paint", "build", {"surface_id": "id", "material_id": "id"}),
    op("build.set_floor", "build", {"floor": "floor"}),
    op("build.undo", "build", effect="undo"), op("build.redo", "build", effect="redo"),
]
CATALOG = {item.name: item for item in _ITEMS}


def get_operation(name):
    try:
        return CATALOG[name]
    except (KeyError, TypeError) as exc:
        raise FoundryError("unsupported_operation", "No raw-input fallback exists for this operation.", operation=name) from exc


def capabilities(backend="windows"):
    if backend not in ("windows", "simulation"):
        raise FoundryError("unsupported_backend", "Choose windows or simulation.")
    return {"ok": True, "backend": backend, "schema": 1, "real_game_control_ready": False,
            "operations": [item.public(backend) for item in _ITEMS],
            "passive_windows_inspection": True, "offline_mod_tools": True,
            "limitations": ["Simulation results never qualify a real operation.",
                            "Launch may activate the game window; it is not non-disruptive.",
                            "No security/UAC bypass, generic shell or forced process termination."]}
