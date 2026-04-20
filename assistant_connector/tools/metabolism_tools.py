from __future__ import annotations

import os

from assistant_connector.memory_store import ConversationMemoryStore

_ACTIVITY_LEVEL_FACTORS = {
    "sedentario": 1.2,
    "sedentário": 1.2,
    "leve": 1.375,
    "moderado": 1.55,
    "alto": 1.725,
    "muito_alto": 1.9,
    "muito alto": 1.9,
}
_SEX_ALIASES = {
    "masculino": "male",
    "homem": "male",
    "male": "male",
    "feminino": "female",
    "mulher": "female",
    "female": "female",
    "outro": "other",
    "other": "other",
}


def _build_memory_store() -> ConversationMemoryStore:
    default_memory_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "assistant_memory.sqlite3")
    )
    resolved_memory_path = os.getenv("ASSISTANT_MEMORY_PATH", default_memory_path)
    return ConversationMemoryStore(resolved_memory_path)


def _read_float(arguments, key, alias=None):
    raw_value = arguments.get(key)
    if raw_value is None and alias:
        raw_value = arguments.get(alias)
    if raw_value in (None, ""):
        return None
    try:
        return float(str(raw_value).replace(",", "."))
    except ValueError as error:
        raise ValueError(f"{key} must be a valid number") from error


def _read_int(arguments, key, alias=None):
    raw_value = arguments.get(key)
    if raw_value is None and alias:
        raw_value = arguments.get(alias)
    if raw_value in (None, ""):
        return None
    try:
        return int(raw_value)
    except ValueError as error:
        raise ValueError(f"{key} must be a valid integer") from error


def _resolve_activity_factor(arguments):
    explicit_factor = _read_float(arguments, "fator_atividade", "activity_factor")
    if explicit_factor is not None:
        if explicit_factor <= 0:
            raise ValueError("fator_atividade must be greater than zero")
        return explicit_factor, None

    activity_level = str(
        arguments.get("nivel_atividade", arguments.get("activity_level", ""))
    ).strip().lower()
    if not activity_level:
        return None, None
    if activity_level not in _ACTIVITY_LEVEL_FACTORS:
        supported = ", ".join(sorted(_ACTIVITY_LEVEL_FACTORS.keys()))
        raise ValueError(f"nivel_atividade must be one of: {supported}")
    return _ACTIVITY_LEVEL_FACTORS[activity_level], activity_level


def _normalize_sex(arguments):
    raw_sex = str(arguments.get("sexo", arguments.get("sex", ""))).strip().lower()
    if not raw_sex:
        return None
    if raw_sex not in _SEX_ALIASES:
        raise ValueError("sexo must be one of: masculino, feminino, outro")
    return _SEX_ALIASES[raw_sex]


def _build_metabolism_estimation(arguments):
    provided_bmr = _read_float(arguments, "metabolismo_basal", "bmr")
    weight_kg = _read_float(arguments, "peso_kg", "weight_kg")
    height_cm = _read_float(arguments, "altura_cm", "height_cm")
    age = _read_int(arguments, "idade", "age")
    sex = _normalize_sex(arguments)
    body_fat_percentage = _read_float(
        arguments, "gordura_corporal_percentual", "body_fat_percentage"
    )
    provided_tdee = _read_float(arguments, "gasto_total_diario", "tdee")
    activity_factor, activity_level = _resolve_activity_factor(arguments)

    if provided_bmr is not None and provided_bmr <= 0:
        raise ValueError("metabolismo_basal must be greater than zero")
    if provided_tdee is not None and provided_tdee <= 0:
        raise ValueError("gasto_total_diario must be greater than zero")
    if weight_kg is not None and weight_kg <= 0:
        raise ValueError("peso_kg must be greater than zero")
    if height_cm is not None and height_cm <= 0:
        raise ValueError("altura_cm must be greater than zero")
    if age is not None and age <= 0:
        raise ValueError("idade must be greater than zero")
    if body_fat_percentage is not None and not (0 < body_fat_percentage < 100):
        raise ValueError("gordura_corporal_percentual must be between 0 and 100")

    if provided_bmr is not None:
        bmr = provided_bmr
        formula = "provided_by_user"
    elif (
        weight_kg is not None
        and height_cm is not None
        and age is not None
        and sex in {"male", "female"}
    ):
        adjustment = 5 if sex == "male" else -161
        bmr = (10.0 * weight_kg) + (6.25 * height_cm) - (5.0 * age) + adjustment
        formula = "mifflin_st_jeor"
    elif weight_kg is not None and body_fat_percentage is not None:
        lean_mass = weight_kg * (1.0 - (body_fat_percentage / 100.0))
        bmr = 370.0 + (21.6 * lean_mass)
        formula = "katch_mcardle"
    else:
        raise ValueError(
            "Provide metabolismo_basal or enough data for calculation: "
            "peso_kg+altura_cm+idade+sexo, or peso_kg+gordura_corporal_percentual."
        )
    if bmr <= 0:
        raise ValueError(
            f"Calculated BMR ({bmr:.2f}) is invalid. Please check your input values."
        )

    tdee = provided_tdee
    if tdee is None and activity_factor is not None:
        tdee = bmr * activity_factor
    if tdee is not None and activity_factor is None and bmr > 0:
        activity_factor = tdee / bmr

    return {
        "bmr": round(float(bmr), 2),
        "tdee": round(float(tdee), 2) if tdee is not None else None,
        "activity_factor": round(float(activity_factor), 4) if activity_factor is not None else None,
        "activity_level": activity_level,
        "formula": formula,
        "inputs_used": {
            "peso_kg": weight_kg,
            "altura_cm": height_cm,
            "idade": age,
            "sexo": sex,
            "gordura_corporal_percentual": body_fat_percentage,
            "metabolismo_basal": provided_bmr,
            "gasto_total_diario": provided_tdee,
        },
    }


def calculate_metabolism_profile(arguments, _context):
    estimation = _build_metabolism_estimation(arguments)
    return {
        "status": "calculated",
        **estimation,
    }


def register_metabolism_profile(arguments, context):
    estimation = _build_metabolism_estimation(arguments)
    measured_at = arguments.get("data_referencia", arguments.get("measured_at"))
    measured_at_value = str(measured_at).strip() if measured_at is not None else ""
    source = str(arguments.get("fonte", arguments.get("source", "assistant"))).strip().lower() or "assistant"
    notes = str(arguments.get("notas", arguments.get("notes", ""))).strip()

    memory_store = _build_memory_store()
    entry = memory_store.create_metabolism_record(
        user_id=str(context.user_id),
        bmr=estimation["bmr"],
        tdee=estimation["tdee"],
        activity_factor=estimation["activity_factor"],
        weight_kg=estimation["inputs_used"]["peso_kg"],
        height_cm=estimation["inputs_used"]["altura_cm"],
        age=estimation["inputs_used"]["idade"],
        sex=estimation["inputs_used"]["sexo"],
        body_fat_percentage=estimation["inputs_used"]["gordura_corporal_percentual"],
        source=source,
        notes=notes,
        measured_at=measured_at_value or None,
    )

    return {
        "status": "created",
        "entry": entry,
        "calculation": estimation,
    }


def get_metabolism_history(arguments, context):
    limit = min(max(int(arguments.get("limit", 10)), 1), 50)
    memory_store = _build_memory_store()
    entries = memory_store.list_metabolism_history(
        user_id=str(context.user_id),
        limit=limit,
    )
    return {
        "total": len(entries),
        "latest": entries[0] if entries else None,
        "entries": entries,
    }
