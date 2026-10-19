"""Feature vocabulary: the bridge between a free-text procurement case and the
structured `applies_if` field of each requirement.

Keywords are the deterministic baseline extractor. They catch explicit
mentions ("hydraulic", "400 V") but miss implicit ones ("door slammed 200,000
times" implies noise) - that gap is what the AI layer is evaluated on.
"""

FEATURES: dict[str, dict] = {
    "electrical": {
        "desc": "Electrical supply, motors, drives or control cabinet",
        "kw": ["electric", "400 v", "230 v", "motor", "cabinet", "frequency converter",
               "servo", "drive"],
    },
    "hydraulic": {
        "desc": "Hydraulic power unit, cylinders or pressurised oil circuits",
        "kw": ["hydraulic", "hpu", "power unit"],
    },
    "pneumatic": {
        "desc": "Compressed air / pneumatic actuators",
        "kw": ["pneumatic", "compressed air"],
    },
    "moving_parts": {
        "desc": "Rotating or moving machine parts (shafts, axes, conveyors, flywheels)",
        "kw": ["rotat", "rpm", "spindle", "shaft", "conveyor", "flywheel", "linear axis"],
    },
    "robot": {
        "desc": "Industrial or collaborative robot",
        "kw": ["robot", "cobot"],
    },
    "operator_access": {
        "desc": "Operators load, mount or enter the machine during normal use",
        "kw": ["operator", "manual loading", "mounted manually", "load port", "enter"],
    },
    "hot_surfaces": {
        "desc": "Extreme temperatures (hot or cold) of surfaces, fluids or test objects",
        "kw": ["°c", "heated", "temperature", "climate chamber", "hot"],
    },
    "noise": {
        "desc": "Significant noise emission at operator positions",
        "kw": ["noise", "db(a)", "loud"],
    },
    "software_control": {
        "desc": "Programmable control system (PLC, safety PLC, HMI, test software)",
        "kw": ["plc", "hmi", "software", "controller", "control system"],
    },
    "remote_access": {
        "desc": "Network connection, remote support or remote diagnostics",
        "kw": ["remote", "vpn", "network", "mes", "ethernet"],
    },
    "lifting": {
        "desc": "Lifting of heavy test objects (crane, hoist, lifting points)",
        "kw": ["crane", "hoist", "lifted", "lifting"],
    },
    "pressure_equipment": {
        "desc": "Pressure vessels, accumulators or refrigerant circuits",
        "kw": ["accumulator", "pressure vessel", "refrigerant", "600 bar"],
    },
    "flammable": {
        "desc": "Flammable fluids, fuels or explosive atmospheres",
        "kw": ["fuel", "diesel", "flammable", "hydrogen", "explosive", "atex"],
    },
}


def describe() -> str:
    return "\n".join(f"- {k}: {v['desc']}" for k, v in FEATURES.items())
