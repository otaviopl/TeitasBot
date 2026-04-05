import datetime
import logging
import os
import re

import requests as _requests_lib
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils import load_credentials
from utils.timezone_utils import (
    get_configured_timezone,
    today_in_configured_timezone,
    today_iso_in_configured_timezone,
)

_NOTION_TIMEOUT = int(os.getenv("NOTION_API_TIMEOUT", "30"))


def _build_session() -> _requests_lib.Session:
    """Create a requests.Session with retry and connection pooling."""
    max_retries = int(os.getenv("NOTION_API_MAX_RETRIES", "2"))
    session = _requests_lib.Session()
    retry = Retry(
        total=max_retries,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PATCH"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
    session.mount("https://", adapter)
    return session


# Shadow the `requests` name at module scope so that existing code
# (`requests.post(...)`) and test mocks
# (`@patch("notion_connector.notion_connector.requests.post")`) keep working.
requests = _build_session()  # type: ignore[assignment]


def collect_tasks_from_control_panel(n_days=0, project_logger=None, user_id=None, credential_store=None):
    """
    Connect to Notion API and collect Tasks from 'Control Panel' database.
    """
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_database_id.")
    today = today_in_configured_timezone()
    cutoff_day = today + datetime.timedelta(days=max(n_days, 0) + 1)
    cutoff_datetime = datetime.datetime.combine(
        cutoff_day,
        datetime.time.min,
        tzinfo=get_configured_timezone(),
    ).isoformat()

    query_candidates = [
        {
            "url": f"https://api.notion.com/v1/data_sources/{notion_credentials['database_id']}/query",
            "notion_version": os.getenv("NOTION_VERSION", "2025-09-03"),
        },
        {
            "url": f"https://api.notion.com/v1/databases/{notion_credentials['database_id']}/query",
            "notion_version": "2022-06-28",
        },
    ]

    payload = {
        "filter": {
            "and": [
                {
                    "property": "DONE",
                    "checkbox": {"equals": False},
                },
                {
                    "property": "When",
                    "date": {"before": cutoff_datetime},
                },
            ],
        },
        "sorts": [{"property": "When", "direction": "ascending"}],
        "page_size": 100,
    }

    project_logger.info("Collecting pending tasks from Notion (including overdue tasks)...")

    all_task_data = []
    next_cursor = None
    has_more = True
    selected_candidate = None
    while has_more:
        request_payload = payload.copy()
        if next_cursor:
            request_payload["start_cursor"] = next_cursor

        if selected_candidate is None:
            last_error = None
            for candidate in query_candidates:
                headers = {
                    "accept": "application/json",
                    "Authorization": "Bearer " + notion_credentials["api_key"] + "",
                    "Notion-Version": candidate["notion_version"],
                    "content-type": "application/json",
                }
                response = requests.post(candidate["url"], json=request_payload, headers=headers, timeout=_NOTION_TIMEOUT)
                if response.status_code in (400, 404):
                    response_code = response.json().get("code", "")
                    if response_code in ("invalid_request_url", "object_not_found"):
                        last_error = response
                        continue
                    last_error = response
                response.raise_for_status()
                selected_candidate = candidate
                break
            if selected_candidate is None:
                last_error.raise_for_status()
        else:
            headers = {
                "accept": "application/json",
                "Authorization": "Bearer " + notion_credentials["api_key"] + "",
                "Notion-Version": selected_candidate["notion_version"],
                "content-type": "application/json",
            }
            response = requests.post(selected_candidate["url"], json=request_payload, headers=headers, timeout=_NOTION_TIMEOUT)
            response.raise_for_status()

        data = response.json()

        for task in data.get("results", []):
            properties = task.get("properties", {})
            task_title = (
                properties.get("Task", {}).get("title", [])
                or properties.get("Name", {}).get("title", [])
            )
            deadline = (
                properties.get("Deadline", {}).get("date")
                or properties.get("When", {}).get("date")
            )
            project = properties.get("Project", {}).get("select")
            tags_property = properties.get("Tags", {})
            tags = []
            if tags_property.get("type") == "multi_select":
                tags = [tag.get("name") for tag in tags_property.get("multi_select", []) if tag.get("name")]
            elif tags_property.get("type") == "select":
                tag_name = tags_property.get("select", {}).get("name")
                tags = [tag_name] if tag_name else []

            if not task_title or not deadline or not deadline.get("start"):
                project_logger.warning("Skipping malformed Notion task payload: %s", task.get("id"))
                continue

            all_task_data.append(
                {
                    "id": task.get("id"),
                    "page_url": task.get("url"),
                    "name": task_title[0].get("plain_text") or task_title[0]["text"]["content"],
                    "deadline": deadline["start"],
                    "project": project["name"] if project else "No project",
                    "tags": tags,
                }
            )

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    sorted_tasks = sorted(all_task_data, key=lambda d: d['deadline'])

    return sorted_tasks


def _build_create_task_payload(task_data, title_property, date_property):
    return {
        "parent": {"database_id": task_data["database_id"]},
        "properties": {
            title_property: {
                "title": [{"text": {"content": task_data["task_name"]}}]
            },
            date_property: {
                "date": {"start": task_data["due_date"]}
            },
            "Project": {
                "select": {"name": task_data["project"]}
            },
            "Tags": {
                "multi_select": [{"name": tag} for tag in task_data["tags"]]
            },
            "DONE": {
                "checkbox": False
            },
        },
    }


def create_task_in_control_panel(task_data, project_logger=None, user_id=None, credential_store=None):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_database_id.")

    create_data = {
        "database_id": notion_credentials["database_id"],
        "task_name": task_data["task_name"],
        "project": task_data["project"],
        "due_date": task_data["due_date"],
        "tags": task_data.get("tags", []),
    }
    headers = {
        "accept": "application/json",
        "Authorization": "Bearer " + notion_credentials["api_key"] + "",
        "Notion-Version": "2022-06-28",
        "content-type": "application/json",
    }
    payload_candidates = [
        _build_create_task_payload(create_data, "Task", "When"),
        _build_create_task_payload(create_data, "Name", "When"),
        _build_create_task_payload(create_data, "Task", "Deadline"),
        _build_create_task_payload(create_data, "Name", "Deadline"),
    ]

    last_error = None
    for payload in payload_candidates:
        response = requests.post(
            "https://api.notion.com/v1/pages",
            json=payload,
            headers=headers,
            timeout=_NOTION_TIMEOUT,
        )
        if response.status_code == 400 and response.json().get("code") == "validation_error":
            last_error = response
            continue
        response.raise_for_status()
        result = response.json()
        return {
            "id": result.get("id"),
            "url": result.get("url"),
            "task_name": create_data["task_name"],
            "project": create_data["project"],
            "due_date": create_data["due_date"],
            "tags": create_data["tags"],
        }

    if last_error is not None:
        last_error.raise_for_status()
    raise RuntimeError("Failed to create task in Notion")


def _find_property_name(properties, preferred_names, accepted_types):
    for property_name in preferred_names:
        metadata = properties.get(property_name, {})
        if metadata.get("type") in accepted_types:
            return property_name, metadata.get("type")
    for property_name, metadata in properties.items():
        if metadata.get("type") in accepted_types:
            return property_name, metadata.get("type")
    return None, None


def _collect_page_block_ids(page_id, headers):
    block_ids = []
    next_cursor = None
    has_more = True
    while has_more:
        params = {"page_size": 100}
        if next_cursor:
            params["start_cursor"] = next_cursor
        response = requests.get(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=headers,
            params=params,
            timeout=_NOTION_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        for block in payload.get("results", []):
            block_id = block.get("id")
            if block_id:
                block_ids.append(block_id)
        has_more = bool(payload.get("has_more"))
        next_cursor = payload.get("next_cursor")
    return block_ids


def _replace_page_content(page_id, headers):
    for block_id in _collect_page_block_ids(page_id, headers):
        archive_response = requests.patch(
            f"https://api.notion.com/v1/blocks/{block_id}",
            json={"archived": True},
            headers=headers,
            timeout=_NOTION_TIMEOUT,
        )
        archive_response.raise_for_status()


def update_notion_page(page_data, project_logger=None, user_id=None, credential_store=None):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_database_id.")

    item_type = str(page_data.get("item_type", "")).strip().lower()
    if item_type not in {"task", "card"}:
        raise ValueError("item_type must be 'task' or 'card'")

    page_id = _normalize_notion_object_id(page_data.get("page_id"))
    if not page_id:
        raise ValueError("page_id is required")
    if not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        page_id,
    ):
        raise ValueError("page_id must be a Notion page ID or URL containing a valid page ID")

    headers = {
        "accept": "application/json",
        "Authorization": "Bearer " + notion_credentials["api_key"] + "",
        "Notion-Version": "2022-06-28",
        "content-type": "application/json",
    }
    page_response = requests.get(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=headers,
        timeout=_NOTION_TIMEOUT,
    )
    page_response.raise_for_status()
    page_payload = page_response.json()
    properties = page_payload.get("properties", {})
    if not isinstance(properties, dict):
        raise RuntimeError("Invalid Notion page properties payload")

    updates = {}
    updated_fields = []
    content = None
    if "content" in page_data:
        raw_content = str(page_data.get("content", ""))
        if raw_content.strip():
            content = raw_content
    content_mode = str(page_data.get("content_mode", "append")).strip().lower() or "append"
    if content_mode not in {"append", "replace"}:
        raise ValueError("content_mode must be 'append' or 'replace'")

    if item_type == "task":
        if "task_name" in page_data:
            title_property, _ = _find_property_name(properties, ("Task", "Name"), {"title"})
            if not title_property:
                raise ValueError("Task title property not found in Notion page")
            task_name = str(page_data.get("task_name", "")).strip()
            updates[title_property] = {"title": [{"text": {"content": task_name}}]}
            updated_fields.append("task_name")

        if "due_date" in page_data:
            date_property, _ = _find_property_name(properties, ("When", "Deadline"), {"date"})
            if not date_property:
                raise ValueError("Task date property not found in Notion page")
            updates[date_property] = {"date": {"start": page_data.get("due_date")}}
            updated_fields.append("due_date")

        if "project" in page_data:
            project_property, _ = _find_property_name(properties, ("Project",), {"select"})
            if not project_property:
                raise ValueError("Task project property not found in Notion page")
            project_name = str(page_data.get("project", "")).strip()
            updates[project_property] = {"select": {"name": project_name} if project_name else None}
            updated_fields.append("project")

        if "tags" in page_data:
            tags_property, tags_type = _find_property_name(properties, ("Tags",), {"multi_select", "select"})
            if not tags_property:
                raise ValueError("Task tags property not found in Notion page")
            tag_names = [str(tag).strip() for tag in page_data.get("tags", []) if str(tag).strip()]
            if tags_type == "multi_select":
                updates[tags_property] = {"multi_select": [{"name": tag} for tag in tag_names]}
            else:
                updates[tags_property] = {"select": {"name": tag_names[0]} if tag_names else None}
            updated_fields.append("tags")

        if "done" in page_data:
            done_property, _ = _find_property_name(properties, ("DONE",), {"checkbox"})
            if not done_property:
                raise ValueError("Task checkbox property not found in Notion page")
            updates[done_property] = {"checkbox": bool(page_data.get("done"))}
            updated_fields.append("done")

    if item_type == "card":
        if "note_name" in page_data:
            title_property, _ = _find_property_name(properties, ("Name", "Type"), {"title"})
            if not title_property:
                raise ValueError("Card title property not found in Notion page")
            note_name = str(page_data.get("note_name", "")).strip()
            updates[title_property] = {"title": [{"text": {"content": note_name}}]}
            updated_fields.append("note_name")

        if "tag" in page_data:
            tag_property, tag_type = _find_property_name(properties, ("Tags", "Type"), {"multi_select", "select"})
            if not tag_property:
                raise ValueError("Card tag property not found in Notion page")
            tag_name = str(page_data.get("tag", "")).strip()
            if tag_type == "multi_select":
                updates[tag_property] = {"multi_select": [{"name": tag_name}] if tag_name else []}
            else:
                updates[tag_property] = {"select": {"name": tag_name} if tag_name else None}
            updated_fields.append("tag")

        if "observations" in page_data:
            observations_property, _ = _find_property_name(
                properties,
                ("Observações", "Observacoes", "Observations"),
                {"rich_text"},
            )
            if not observations_property:
                raise ValueError("Card observations property not found in Notion page")
            updates[observations_property] = {
                "rich_text": _build_notion_rich_text_chunks(str(page_data.get("observations", ""))),
            }
            updated_fields.append("observations")

        if "url" in page_data:
            url_property, _ = _find_property_name(properties, ("URL",), {"url"})
            if not url_property:
                raise ValueError("Card url property not found in Notion page")
            external_url = str(page_data.get("url", "")).strip()
            updates[url_property] = {"url": external_url or None}
            updated_fields.append("url")

        if "date" in page_data:
            date_property, _ = _find_property_name(properties, ("Date", "Created"), {"date"})
            if not date_property:
                raise ValueError("Card date property not found in Notion page")
            updates[date_property] = {"date": {"start": page_data.get("date")}}
            updated_fields.append("date")

    if not updates and content is None:
        raise ValueError("No fields to update")

    result = page_payload
    if updates:
        response = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            json={"properties": updates},
            headers=headers,
            timeout=_NOTION_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()

    if content is not None:
        if content_mode == "replace":
            _replace_page_content(page_id, headers)
        children = _build_note_children(content)
        append_response = requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            json={"children": children},
            headers=headers,
            timeout=_NOTION_TIMEOUT,
        )
        append_response.raise_for_status()
        updated_fields.append("content")

    return {
        "id": result.get("id"),
        "page_url": result.get("url"),
        "item_type": item_type,
        "updated_fields": updated_fields,
    }


def _resolve_notion_database_id(store_key, env_var, project_logger, user_id=None, store=None):
    from utils.load_credentials import load_notion_db_id
    db_id = load_notion_db_id(store_key, env_var, project_logger, user_id=user_id, store=store)
    if db_id:
        return _normalize_notion_object_id(db_id)
    error_message = f"Missing required environment variable: {env_var}"
    project_logger.error(error_message)
    raise ValueError(error_message)


def _get_notes_database_id(project_logger, user_id=None, store=None):
    return _resolve_notion_database_id("notion_notes_db_id", "NOTION_NOTES_DB_ID", project_logger, user_id=user_id, store=store)


def _get_expenses_database_id(project_logger, user_id=None, store=None):
    return _resolve_notion_database_id("notion_expenses_db_id", "NOTION_EXPENSES_DB_ID", project_logger, user_id=user_id, store=store)


def _get_monthly_bills_database_id(project_logger, user_id=None, store=None):
    return _resolve_notion_database_id("notion_monthly_bills_db_id", "NOTION_MONTHLY_BILLS_DB_ID", project_logger, user_id=user_id, store=store)


def _get_meals_database_id(project_logger, user_id=None, store=None):
    return _resolve_notion_database_id("notion_meals_db_id", "NOTION_MEALS_DB_ID", project_logger, user_id=user_id, store=store)


def _get_exercises_database_id(project_logger, user_id=None, store=None):
    return _resolve_notion_database_id("notion_exercises_db_id", "NOTION_EXERCISES_DB_ID", project_logger, user_id=user_id, store=store)


_MEAL_UNIT_ALIASES = {
    "g": "g",
    "grama": "g",
    "gramas": "g",
    "gr": "g",
    "kg": "kg",
    "quilo": "kg",
    "quilos": "kg",
    "ml": "ml",
    "mililitro": "ml",
    "mililitros": "ml",
    "l": "l",
    "litro": "l",
    "litros": "l",
    "un": "unit",
    "und": "unit",
    "unidade": "unit",
    "unidades": "unit",
    "porcao": "portion",
    "porcoes": "portion",
    "porcaoes": "portion",
    "porcao(s)": "portion",
    "xicara": "cup",
    "xicaras": "cup",
    "colher": "tbsp",
    "colher de sopa": "tbsp",
    "colheres de sopa": "tbsp",
    "colher de cha": "tsp",
    "colheres de cha": "tsp",
}


def _normalize_notion_object_id(raw_value):
    value = str(raw_value or "").strip()
    if not value:
        return value

    dashed_match = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        value,
    )
    if dashed_match:
        return dashed_match.group(0).lower()

    compact_match = re.search(r"[0-9a-fA-F]{32}", value)
    if compact_match:
        compact = compact_match.group(0).lower()
        return (
            f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-"
            f"{compact[16:20]}-{compact[20:32]}"
        )

    return value


def _normalize_text_for_lookup(value):
    text = str(value or "").strip().lower()
    replacements = str.maketrans(
        {
            "á": "a",
            "à": "a",
            "â": "a",
            "ã": "a",
            "ä": "a",
            "é": "e",
            "è": "e",
            "ê": "e",
            "ë": "e",
            "í": "i",
            "ì": "i",
            "î": "i",
            "ï": "i",
            "ó": "o",
            "ò": "o",
            "ô": "o",
            "õ": "o",
            "ö": "o",
            "ú": "u",
            "ù": "u",
            "û": "u",
            "ü": "u",
            "ç": "c",
        }
    )
    text = text.translate(replacements)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_first_float(raw_value):
    match = re.search(r"(-?[0-9]+(?:[.,][0-9]+)?)", str(raw_value or ""))
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _normalize_quantity_unit(raw_unit):
    normalized_unit = _normalize_text_for_lookup(raw_unit)
    if not normalized_unit:
        return "unit"
    for alias, normalized in _MEAL_UNIT_ALIASES.items():
        if normalized_unit == alias or normalized_unit.startswith(f"{alias} "):
            return normalized
    return normalized_unit.split(" ")[0]


def _parse_quantity_details(quantity):
    quantity_text = str(quantity or "").strip()
    if not quantity_text:
        raise ValueError("quantity is required")
    amount = _extract_first_float(quantity_text)
    if amount is None:
        raise ValueError("quantity must include a numeric value")
    if amount <= 0:
        raise ValueError("quantity must be greater than zero")
    unit_match = re.search(r"[-+]?[0-9]+(?:[.,][0-9]+)?\s*([^\d].*)?$", quantity_text)
    raw_unit = unit_match.group(1).strip() if unit_match and unit_match.group(1) else ""
    unit = _normalize_quantity_unit(raw_unit)
    return {
        "raw": quantity_text,
        "amount": amount,
        "unit": unit,
    }


_WEIGHT_UNITS = {"g", "kg"}
_VOLUME_UNITS = {"ml", "l"}
_DISCRETE_UNITS = {"unit", "portion", "cup", "tbsp", "tsp"}

_UNIT_DISPLAY_LABELS = {
    "g": "g",
    "kg": "kg",
    "ml": "ml",
    "l": "l",
    "unit": "un",
    "portion": "porção",
    "cup": "xícara",
    "tbsp": "colher de sopa",
    "tsp": "colher de chá",
}


def _normalize_quantity(quantity_details):
    """Normalize parsed quantity into a base unit for its family.

    Weight  -> grams (g)
    Volume  -> milliliters (ml)
    Discrete / unknown -> kept as-is
    """
    amount = float(quantity_details["amount"])
    unit = quantity_details["unit"]

    if unit == "g":
        normalized_amount = amount
        normalized_unit = "g"
    elif unit == "kg":
        normalized_amount = amount * 1000.0
        normalized_unit = "g"
    elif unit == "ml":
        normalized_amount = amount
        normalized_unit = "ml"
    elif unit == "l":
        normalized_amount = amount * 1000.0
        normalized_unit = "ml"
    else:
        normalized_amount = amount
        normalized_unit = unit

    if normalized_amount <= 0:
        raise ValueError("quantity must be greater than zero")
    return {
        "amount": round(normalized_amount, 2),
        "unit": normalized_unit,
    }


def _format_quantity(normalized_amount, normalized_unit):
    """Format a normalized quantity for human-readable display."""
    value = round(float(normalized_amount), 2)
    if value.is_integer():
        display_value = str(int(value))
    else:
        display_value = f"{value:.2f}".rstrip("0").rstrip(".")
    label = _UNIT_DISPLAY_LABELS.get(normalized_unit, normalized_unit)
    return f"{display_value} {label}"


def _build_create_note_payload(note_data, tags_property_type, observations_property):
    tag_payload = (
        {"multi_select": [{"name": note_data["tag"]}]}
        if tags_property_type == "multi_select"
        else {"select": {"name": note_data["tag"]}}
    )

    properties = {
        "Name": {
            "title": [{"text": {"content": note_data["note_name"]}}],
        },
        "Date": {
            "date": {"start": note_data["date"]},
        },
        "Tags": tag_payload,
        "URL": {
            "url": note_data["url"],
        },
    }

    if note_data["observations"]:
        properties[observations_property] = {
            "rich_text": _build_notion_rich_text_chunks(note_data["observations"]),
        }

    return properties


def _build_create_note_properties(
    note_data,
    title_property,
    date_property,
    tags_property=None,
    tags_property_type="multi_select",
    observations_property=None,
    url_property=None,
):
    properties = {
        title_property: {
            "title": [{"text": {"content": note_data["note_name"]}}],
        },
        date_property: {
            "date": {"start": note_data["date"]},
        },
    }

    if tags_property:
        if tags_property_type == "select":
            properties[tags_property] = {"select": {"name": note_data["tag"]}}
        else:
            properties[tags_property] = {"multi_select": [{"name": note_data["tag"]}]}

    if url_property and note_data["url"]:
        properties[url_property] = {"url": note_data["url"]}

    if observations_property and note_data["observations"]:
        properties[observations_property] = {
            "rich_text": _build_notion_rich_text_chunks(note_data["observations"]),
        }

    return properties


def _build_notion_rich_text_chunks(text, chunk_size=1800):
    value = str(text or "")
    if not value:
        return []

    segments = _parse_markdown_segments(value)
    rich_text = []
    for segment in segments:
        segment_text = segment["text"]
        if not segment_text:
            continue
        chunks = [segment_text[i : i + chunk_size] for i in range(0, len(segment_text), chunk_size)]
        for chunk in chunks:
            rich_item = {
                "type": "text",
                "text": {
                    "content": chunk,
                },
                "annotations": {
                    "bold": bool(segment.get("bold", False)),
                    "italic": bool(segment.get("italic", False)),
                    "strikethrough": False,
                    "underline": False,
                    "code": bool(segment.get("code", False)),
                    "color": "default",
                },
                "plain_text": chunk,
            }
            if segment.get("url"):
                rich_item["text"]["link"] = {"url": segment["url"]}
            rich_text.append(rich_item)
    return rich_text


def _parse_markdown_segments(text):
    pattern = re.compile(
        r"(\[([^\]]+)\]\((https?://[^)\s]+)\)|\*\*([^*]+)\*\*|`([^`]+)`|\*([^*]+)\*)"
    )
    segments = []
    cursor = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        if start > cursor:
            segments.append({"text": text[cursor:start]})
        if match.group(2) is not None:
            segments.append({"text": match.group(2), "url": match.group(3)})
        elif match.group(4) is not None:
            segments.append({"text": match.group(4), "bold": True})
        elif match.group(5) is not None:
            segments.append({"text": match.group(5), "code": True})
        elif match.group(6) is not None:
            segments.append({"text": match.group(6), "italic": True})
        cursor = end
    if cursor < len(text):
        segments.append({"text": text[cursor:]})
    return segments


def _build_note_children(text):
    value = str(text or "")
    if not value:
        return []

    blocks = []
    for raw_line in value.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue

        if line.startswith("### "):
            block_type = "heading_3"
            content = line[4:]
        elif line.startswith("## "):
            block_type = "heading_2"
            content = line[3:]
        elif line.startswith("# "):
            block_type = "heading_1"
            content = line[2:]
        elif line.startswith("- ") or line.startswith("* "):
            block_type = "bulleted_list_item"
            content = line[2:]
        elif re.match(r"^\d+\.\s+", line):
            block_type = "numbered_list_item"
            content = re.sub(r"^\d+\.\s+", "", line, count=1)
        else:
            block_type = "paragraph"
            content = line

        rich_text = _build_notion_rich_text_chunks(content)
        if not rich_text:
            continue
        blocks.append(
            {
                "object": "block",
                "type": block_type,
                block_type: {"rich_text": rich_text},
            }
        )

    if blocks:
        return blocks

    fallback = _build_notion_rich_text_chunks(value)
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": fallback},
        }
    ] if fallback else []


def _fetch_database_schema(database_id, api_key):
    headers = {
        "accept": "application/json",
        "Authorization": "Bearer " + api_key + "",
        "Notion-Version": "2022-06-28",
    }
    response = requests.get(
        f"https://api.notion.com/v1/databases/{database_id}",
        headers=headers,
        timeout=_NOTION_TIMEOUT,
    )
    if response.status_code != 200:
        return {}
    payload = response.json()
    properties = payload.get("properties", {})
    return properties if isinstance(properties, dict) else {}


def _build_note_payload_from_schema(create_data, schema_properties):
    title_property = None
    for property_name, metadata in schema_properties.items():
        if metadata.get("type") == "title":
            title_property = property_name
            break
    if not title_property:
        return None

    properties = {
        title_property: {
            "title": [{"text": {"content": create_data["note_name"]}}],
        }
    }

    date_property = None
    for preferred in ("Date",):
        if schema_properties.get(preferred, {}).get("type") == "date":
            date_property = preferred
            break
    if not date_property:
        for property_name, metadata in schema_properties.items():
            if metadata.get("type") == "date":
                date_property = property_name
                break
    if date_property:
        properties[date_property] = {"date": {"start": create_data["date"]}}

    tag_properties = []
    for preferred in ("Tags", "Type"):
        metadata = schema_properties.get(preferred, {})
        if metadata.get("type") in ("multi_select", "select"):
            tag_properties.append((preferred, metadata.get("type")))
    if not tag_properties:
        for property_name, metadata in schema_properties.items():
            if metadata.get("type") in ("multi_select", "select"):
                tag_properties.append((property_name, metadata.get("type")))
                break
    for property_name, property_type in tag_properties:
        if property_type == "multi_select":
            properties[property_name] = {"multi_select": [{"name": create_data["tag"]}]}
        else:
            properties[property_name] = {"select": {"name": create_data["tag"]}}

    if create_data["url"]:
        for preferred in ("URL",):
            if schema_properties.get(preferred, {}).get("type") == "url":
                properties[preferred] = {"url": create_data["url"]}
                break
        else:
            for property_name, metadata in schema_properties.items():
                if metadata.get("type") == "url":
                    properties[property_name] = {"url": create_data["url"]}
                    break

    observations_property = None
    for preferred in ("Observações", "Observacoes", "Observations"):
        if schema_properties.get(preferred, {}).get("type") == "rich_text":
            observations_property = preferred
            break
    if not observations_property:
        for property_name, metadata in schema_properties.items():
            if metadata.get("type") == "rich_text":
                observations_property = property_name
                break
    if observations_property and create_data["observations"]:
        properties[observations_property] = {
            "rich_text": _build_notion_rich_text_chunks(create_data["observations"]),
        }

    payload = {"properties": properties}
    if create_data["observations"] and not observations_property:
        payload["children"] = _build_note_children(create_data["observations"])
    return payload


def create_note_in_notes_db(note_data, project_logger=None, user_id=None, credential_store=None):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_database_id.")
    notes_database_id = _get_notes_database_id(project_logger, user_id=user_id, store=credential_store)

    note_name = str(note_data.get("note_name", "")).strip()
    if not note_name:
        raise ValueError("note_name is required")

    create_data = {
        "database_id": notes_database_id,
        "note_name": note_name,
        "tag": str(note_data.get("tag", "GENERAL")).strip() or "GENERAL",
        "date": str(note_data.get("date", today_iso_in_configured_timezone())).strip(),
        "observations": str(note_data.get("observations", "")).strip(),
        "url": str(note_data.get("url", "")).strip() or None,
    }
    datetime.date.fromisoformat(create_data["date"])
    request_candidates = [
        {
            "notion_version": "2022-06-28",
            "parent": {"database_id": notes_database_id},
        },
        {
            "notion_version": os.getenv("NOTION_VERSION", "2025-09-03"),
            "parent": {"data_source_id": notes_database_id},
        },
    ]
    schema_properties = _fetch_database_schema(notes_database_id, notion_credentials["api_key"])
    last_error = None
    for request_candidate in request_candidates:
        headers = {
            "accept": "application/json",
            "Authorization": "Bearer " + notion_credentials["api_key"] + "",
            "Notion-Version": request_candidate["notion_version"],
            "content-type": "application/json",
        }
        payload_candidates = []
        schema_payload = _build_note_payload_from_schema(create_data, schema_properties)
        if schema_payload:
            payload = {
                "parent": request_candidate["parent"],
                "properties": schema_payload["properties"],
            }
            if schema_payload.get("children"):
                payload["children"] = schema_payload["children"]
            payload_candidates.append(payload)

        property_candidates = [
            _build_create_note_properties(
                create_data,
                title_property="Name",
                date_property="Date",
                tags_property="Tags",
                tags_property_type="multi_select",
                observations_property="Observações",
                url_property="URL",
            ),
            _build_create_note_properties(
                create_data,
                title_property="Name",
                date_property="Created",
                tags_property="Tags",
                tags_property_type="multi_select",
                observations_property="Observações",
                url_property="URL",
            ),
            _build_create_note_properties(
                create_data,
                title_property="Name",
                date_property="Created",
                tags_property="Tags",
                tags_property_type="select",
                observations_property=None,
                url_property=None,
            ),
            _build_create_note_properties(
                create_data,
                title_property="Type",
                date_property="Created",
                tags_property="Tags",
                tags_property_type="multi_select",
                observations_property=None,
                url_property=None,
            ),
            _build_create_note_properties(
                create_data,
                title_property="Type",
                date_property="Created",
                tags_property="Tags",
                tags_property_type="select",
                observations_property=None,
                url_property=None,
            ),
            _build_create_note_properties(
                create_data,
                title_property="Name",
                date_property="Date",
                tags_property="Tags",
                tags_property_type="multi_select",
                observations_property="Observacoes",
                url_property="URL",
            ),
            _build_create_note_properties(
                create_data,
                title_property="Name",
                date_property="Date",
                tags_property=None,
                observations_property=None,
                url_property=None,
            ),
            _build_create_note_properties(
                create_data,
                title_property="Type",
                date_property="Created",
                tags_property=None,
                observations_property=None,
                url_property=None,
            ),
        ]
        for properties in property_candidates:
            payload = {
                "parent": request_candidate["parent"],
                "properties": properties,
            }
            has_observations_property = any(
                key in properties for key in ("Observações", "Observacoes", "Observations")
            )
            if create_data["observations"] and not has_observations_property:
                payload["children"] = _build_note_children(create_data["observations"])
            payload_candidates.append(payload)
        for payload in payload_candidates:
            response = requests.post(
                "https://api.notion.com/v1/pages",
                json=payload,
                headers=headers,
                timeout=_NOTION_TIMEOUT,
            )
            response_payload = {}
            try:
                response_payload = response.json()
            except ValueError:
                response_payload = {}
            if response.status_code in (400, 404):
                response_code = response_payload.get("code", "")
                if response_code in ("validation_error", "object_not_found", "invalid_request"):
                    last_error = response
                    continue
            response.raise_for_status()
            return {
                "id": response_payload.get("id"),
                "page_url": response_payload.get("url"),
                "note_name": create_data["note_name"],
                "tag": create_data["tag"],
                "date": create_data["date"],
                "observations": create_data["observations"],
                "url": create_data["url"],
            }

    if last_error is not None:
        try:
            error_payload = last_error.json()
        except ValueError:
            error_payload = {}
        if error_payload.get("code") == "object_not_found":
            raise ValueError(
                "NOTION_NOTES_DB_ID was not found by the Notion API. "
                "Please verify the exact Notes database/data-source ID and sharing with the integration."
            ) from None
        last_error.raise_for_status()
    raise RuntimeError("Failed to create note in Notion")


def collect_notes_around_today(days_back=5, days_forward=5, project_logger=None, user_id=None, credential_store=None):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_database_id.")
    notes_database_id = _get_notes_database_id(project_logger, user_id=user_id, store=credential_store)

    today = today_in_configured_timezone()
    start_date = (today - datetime.timedelta(days=max(days_back, 0))).isoformat()
    end_date = (today + datetime.timedelta(days=max(days_forward, 0))).isoformat()

    query_candidates = [
        {
            "url": f"https://api.notion.com/v1/data_sources/{notes_database_id}/query",
            "notion_version": os.getenv("NOTION_VERSION", "2025-09-03"),
            "date_property": "Date",
            "date_filter_type": "property_date",
        },
        {
            "url": f"https://api.notion.com/v1/data_sources/{notes_database_id}/query",
            "notion_version": os.getenv("NOTION_VERSION", "2025-09-03"),
            "date_property": "Created",
            "date_filter_type": "property_date",
        },
        {
            "url": f"https://api.notion.com/v1/data_sources/{notes_database_id}/query",
            "notion_version": os.getenv("NOTION_VERSION", "2025-09-03"),
            "date_property": "created_time",
            "date_filter_type": "created_time",
        },
        {
            "url": f"https://api.notion.com/v1/databases/{notes_database_id}/query",
            "notion_version": "2022-06-28",
            "date_property": "Date",
            "date_filter_type": "property_date",
        },
        {
            "url": f"https://api.notion.com/v1/databases/{notes_database_id}/query",
            "notion_version": "2022-06-28",
            "date_property": "Created",
            "date_filter_type": "property_date",
        },
        {
            "url": f"https://api.notion.com/v1/databases/{notes_database_id}/query",
            "notion_version": "2022-06-28",
            "date_property": "created_time",
            "date_filter_type": "created_time",
        },
    ]

    project_logger.info("Collecting notes from Notion around today...")

    all_notes = []
    next_cursor = None
    has_more = True
    selected_candidate = None
    while has_more:
        if selected_candidate is None:
            request_payload = None
        else:
            request_payload = _build_notes_query_payload(
                selected_candidate,
                start_date,
                end_date,
            )
        if next_cursor:
            request_payload["start_cursor"] = next_cursor

        if selected_candidate is None:
            last_error = None
            for candidate in query_candidates:
                request_payload = _build_notes_query_payload(candidate, start_date, end_date)
                if next_cursor:
                    request_payload["start_cursor"] = next_cursor
                headers = {
                    "accept": "application/json",
                    "Authorization": "Bearer " + notion_credentials["api_key"] + "",
                    "Notion-Version": candidate["notion_version"],
                    "content-type": "application/json",
                }
                response = requests.post(candidate["url"], json=request_payload, headers=headers, timeout=_NOTION_TIMEOUT)
                if response.status_code in (400, 404):
                    response_code = response.json().get("code", "")
                    if response_code in ("invalid_request_url", "object_not_found", "validation_error"):
                        last_error = response
                        continue
                    last_error = response
                response.raise_for_status()
                selected_candidate = candidate
                break
            if selected_candidate is None:
                last_error.raise_for_status()
        else:
            headers = {
                "accept": "application/json",
                "Authorization": "Bearer " + notion_credentials["api_key"] + "",
                "Notion-Version": selected_candidate["notion_version"],
                "content-type": "application/json",
            }
            response = requests.post(selected_candidate["url"], json=request_payload, headers=headers, timeout=_NOTION_TIMEOUT)
            response.raise_for_status()

        data = response.json()

        for note in data.get("results", []):
            properties = note.get("properties", {})
            note_title = (
                properties.get("Name", {}).get("title", [])
                or properties.get("Type", {}).get("title", [])
            )
            note_date = (
                properties.get("Date", {}).get("date")
                or properties.get("Created", {}).get("date")
                or {"start": note.get("created_time")}
            )
            tags_property = properties.get("Tags", {})
            tags = []
            if tags_property.get("type") == "multi_select":
                tags = [tag.get("name") for tag in tags_property.get("multi_select", []) if tag.get("name")]
            elif tags_property.get("type") == "select":
                tag_name = tags_property.get("select", {}).get("name")
                tags = [tag_name] if tag_name else []
            elif properties.get("Type", {}).get("type") == "select":
                type_tag = properties.get("Type", {}).get("select", {}).get("name")
                tags = [type_tag] if type_tag else []

            observations_property = properties.get("Observações", {}).get("rich_text")
            if observations_property is None:
                observations_property = properties.get("Observacoes", {}).get("rich_text", [])
            observations = "".join(
                chunk.get("plain_text") or chunk.get("text", {}).get("content", "")
                for chunk in observations_property
            )
            external_url = properties.get("URL", {}).get("url")

            if not note_title or not note_date or not note_date.get("start"):
                project_logger.warning("Skipping malformed Notion note payload: %s", note.get("id"))
                continue

            all_notes.append(
                {
                    "id": note.get("id"),
                    "name": (
                        (note_title[0].get("plain_text") or note_title[0].get("text", {}).get("content"))
                        if note_title else "Untitled note"
                    ),
                    "date": note_date["start"],
                    "tags": tags,
                    "observations": observations,
                    "url": external_url,
                    "page_url": note.get("url"),
                }
            )

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    return sorted(all_notes, key=lambda note: note["date"])


def _build_notes_query_payload(candidate, start_date, end_date):
    if candidate.get("date_filter_type") == "created_time":
        return {
            "filter": {
                "and": [
                    {"timestamp": "created_time", "created_time": {"on_or_after": start_date}},
                    {"timestamp": "created_time", "created_time": {"on_or_before": end_date}},
                ],
            },
            "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
            "page_size": 100,
        }
    return {
        "filter": {
            "and": [
                {
                    "property": candidate["date_property"],
                    "date": {"on_or_after": start_date},
                },
                {
                    "property": candidate["date_property"],
                    "date": {"on_or_before": end_date},
                },
            ],
        },
        "sorts": [
            {"property": candidate["date_property"], "direction": "ascending"},
        ],
        "page_size": 100,
    }


def create_expense_in_expenses_db(expense_data, project_logger=None, user_id=None, credential_store=None):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_expenses_db_id.")
    expenses_database_id = _get_expenses_database_id(project_logger, user_id=user_id, store=credential_store)
    schema_properties = _fetch_database_schema(expenses_database_id, notion_credentials["api_key"])

    expense_name = str(expense_data.get("name", "")).strip()
    if not expense_name:
        raise ValueError("name is required")
    expense_date = str(expense_data.get("date", "")).strip()
    if not expense_date:
        raise ValueError("date is required")
    datetime.date.fromisoformat(expense_date)
    category = str(expense_data.get("category", "")).strip() or "Outros"
    description = str(expense_data.get("description", "")).strip()
    amount = float(expense_data.get("amount", 0))
    if amount <= 0:
        raise ValueError("amount must be greater than zero")

    title_property, _ = _find_property_name(schema_properties, ("Nome", "Name"), {"title"})
    date_property, _ = _find_property_name(schema_properties, ("Data", "Date"), {"date"})
    category_property, category_type = _find_property_name(
        schema_properties,
        ("Categoria", "Category", "Tags", "Type"),
        {"select", "multi_select", "rich_text"},
    )
    amount_property, amount_type = _find_property_name(
        schema_properties,
        ("Valor", "Amount", "Value"),
        {"number", "rich_text"},
    )
    description_property, _ = _find_property_name(
        schema_properties,
        ("Descrição", "Descricao", "Description", "Observações", "Observacoes"),
        {"rich_text"},
    )

    if not title_property:
        raise ValueError("Expense title property not found in expenses database")
    if not date_property:
        raise ValueError("Expense date property not found in expenses database")

    properties = {
        title_property: {"title": [{"text": {"content": expense_name}}]},
        date_property: {"date": {"start": expense_date}},
    }
    if category_property:
        if category_type == "multi_select":
            properties[category_property] = {"multi_select": [{"name": category}]}
        elif category_type == "rich_text":
            properties[category_property] = {"rich_text": _build_notion_rich_text_chunks(category)}
        else:
            properties[category_property] = {"select": {"name": category}}
    if amount_property:
        if amount_type == "number":
            properties[amount_property] = {"number": amount}
        else:
            properties[amount_property] = {"rich_text": _build_notion_rich_text_chunks(f"{amount:.2f}")}
    if description_property:
        stored_description = description
        if not amount_property:
            stored_description = (
                f"amount={amount:.2f}; {description}" if description else f"amount={amount:.2f}"
            )
        properties[description_property] = {"rich_text": _build_notion_rich_text_chunks(stored_description)}

    request_candidates = [
        {
            "notion_version": "2022-06-28",
            "parent": {"database_id": expenses_database_id},
        },
        {
            "notion_version": os.getenv("NOTION_VERSION", "2025-09-03"),
            "parent": {"data_source_id": expenses_database_id},
        },
    ]
    last_error = None
    for request_candidate in request_candidates:
        headers = {
            "accept": "application/json",
            "Authorization": "Bearer " + notion_credentials["api_key"] + "",
            "Notion-Version": request_candidate["notion_version"],
            "content-type": "application/json",
        }
        response = requests.post(
            "https://api.notion.com/v1/pages",
            json={"parent": request_candidate["parent"], "properties": properties},
            headers=headers,
            timeout=_NOTION_TIMEOUT,
        )
        if response.status_code in (400, 404):
            response_code = response.json().get("code", "")
            if response_code in ("validation_error", "object_not_found", "invalid_request"):
                last_error = response
                continue
        response.raise_for_status()
        payload = response.json()
        return {
            "id": payload.get("id"),
            "page_url": payload.get("url"),
            "name": expense_name,
            "date": expense_date,
            "category": category,
            "description": description,
            "amount": round(amount, 2),
        }

    if last_error is not None:
        last_error.raise_for_status()
    raise RuntimeError("Failed to create expense in Notion")


def collect_expenses_from_expenses_db(*, start_date, end_date, project_logger=None, user_id=None, credential_store=None):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_expenses_db_id.")
    expenses_database_id = _get_expenses_database_id(project_logger, user_id=user_id, store=credential_store)

    query_candidates = [
        {
            "url": f"https://api.notion.com/v1/data_sources/{expenses_database_id}/query",
            "notion_version": os.getenv("NOTION_VERSION", "2025-09-03"),
            "date_property": "Data",
        },
        {
            "url": f"https://api.notion.com/v1/databases/{expenses_database_id}/query",
            "notion_version": "2022-06-28",
            "date_property": "Data",
        },
        {
            "url": f"https://api.notion.com/v1/databases/{expenses_database_id}/query",
            "notion_version": "2022-06-28",
            "date_property": "Date",
        },
    ]

    all_expenses = []
    selected_candidate = None
    has_more = True
    next_cursor = None
    while has_more:
        if selected_candidate is None:
            last_error = None
            for candidate in query_candidates:
                request_payload = {
                    "filter": {
                        "and": [
                            {"property": candidate["date_property"], "date": {"on_or_after": start_date}},
                            {"property": candidate["date_property"], "date": {"on_or_before": end_date}},
                        ]
                    },
                    "sorts": [{"property": candidate["date_property"], "direction": "ascending"}],
                    "page_size": 100,
                }
                if next_cursor:
                    request_payload["start_cursor"] = next_cursor
                headers = {
                    "accept": "application/json",
                    "Authorization": "Bearer " + notion_credentials["api_key"] + "",
                    "Notion-Version": candidate["notion_version"],
                    "content-type": "application/json",
                }
                response = requests.post(candidate["url"], json=request_payload, headers=headers, timeout=_NOTION_TIMEOUT)
                if response.status_code in (400, 404):
                    response_code = response.json().get("code", "")
                    if response_code in ("validation_error", "invalid_request_url", "object_not_found"):
                        last_error = response
                        continue
                response.raise_for_status()
                selected_candidate = candidate
                break
            if selected_candidate is None:
                last_error.raise_for_status()
        else:
            request_payload = {
                "filter": {
                    "and": [
                        {"property": selected_candidate["date_property"], "date": {"on_or_after": start_date}},
                        {"property": selected_candidate["date_property"], "date": {"on_or_before": end_date}},
                    ]
                },
                "sorts": [{"property": selected_candidate["date_property"], "direction": "ascending"}],
                "page_size": 100,
            }
            if next_cursor:
                request_payload["start_cursor"] = next_cursor
            headers = {
                "accept": "application/json",
                "Authorization": "Bearer " + notion_credentials["api_key"] + "",
                "Notion-Version": selected_candidate["notion_version"],
                "content-type": "application/json",
            }
            response = requests.post(
                selected_candidate["url"],
                json=request_payload,
                headers=headers,
                timeout=_NOTION_TIMEOUT,
            )
            response.raise_for_status()

        payload = response.json()
        for expense_page in payload.get("results", []):
            properties = expense_page.get("properties", {})
            expense_name = (
                properties.get("Nome", {}).get("title", [])
                or properties.get("Name", {}).get("title", [])
            )
            expense_date = (
                properties.get("Data", {}).get("date")
                or properties.get("Date", {}).get("date")
            )
            category_value = ""
            category_property = (
                properties.get("Categoria")
                or properties.get("Category")
                or properties.get("Tags")
                or {}
            )
            if category_property.get("type") == "select":
                category_value = category_property.get("select", {}).get("name", "")
            elif category_property.get("type") == "multi_select":
                first = category_property.get("multi_select", [])
                category_value = first[0].get("name", "") if first else ""
            elif category_property.get("type") == "rich_text":
                category_value = "".join(
                    chunk.get("plain_text") or chunk.get("text", {}).get("content", "")
                    for chunk in category_property.get("rich_text", [])
                )

            description_property = (
                properties.get("Descrição", {}).get("rich_text")
                or properties.get("Descricao", {}).get("rich_text")
                or properties.get("Description", {}).get("rich_text")
                or properties.get("Observações", {}).get("rich_text")
                or properties.get("Observacoes", {}).get("rich_text")
                or []
            )
            description = "".join(
                chunk.get("plain_text") or chunk.get("text", {}).get("content", "")
                for chunk in description_property
            ).strip()
            amount_property = (
                properties.get("Valor")
                or properties.get("Amount")
                or properties.get("Value")
                or {}
            )
            amount = 0.0
            if amount_property.get("type") == "number":
                amount = float(amount_property.get("number") or 0.0)
            elif amount_property.get("type") == "rich_text":
                amount_text = "".join(
                    chunk.get("plain_text") or chunk.get("text", {}).get("content", "")
                    for chunk in amount_property.get("rich_text", [])
                )
                amount_match = re.search(r"([0-9]+(?:[.,][0-9]{1,2})?)", amount_text)
                if amount_match:
                    amount = float(amount_match.group(1).replace(",", "."))
            if amount == 0.0:
                legacy_match = re.search(
                    r"amount\s*=\s*([0-9]+(?:[.,][0-9]{1,2})?)",
                    description,
                    re.IGNORECASE,
                )
                if legacy_match:
                    amount = float(legacy_match.group(1).replace(",", "."))

            if not expense_name or not expense_date or not expense_date.get("start"):
                continue

            all_expenses.append(
                {
                    "id": expense_page.get("id"),
                    "name": expense_name[0].get("plain_text") or expense_name[0].get("text", {}).get("content"),
                    "date": expense_date.get("start"),
                    "category": category_value or "Outros",
                    "description": re.sub(r"^amount\s*=\s*[0-9]+(?:[.,][0-9]{1,2})?\s*;\s*", "", description, flags=re.IGNORECASE),
                    "amount": round(amount, 2),
                    "page_url": expense_page.get("url"),
                }
            )

        has_more = payload.get("has_more", False)
        next_cursor = payload.get("next_cursor")

    return all_expenses


def create_meal_in_meals_db(meal_data, project_logger=None, user_id=None, credential_store=None):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_meals_db_id.")
    meals_database_id = _get_meals_database_id(project_logger, user_id=user_id, store=credential_store)
    schema_properties = _fetch_database_schema(meals_database_id, notion_credentials["api_key"])

    food_name = str(meal_data.get("food", "")).strip()
    meal_type = str(meal_data.get("meal_type", "")).strip()
    quantity = str(meal_data.get("quantity", "")).strip()
    meal_date = str(meal_data.get("date", today_iso_in_configured_timezone())).strip()
    datetime.date.fromisoformat(meal_date)
    if not food_name:
        raise ValueError("food is required")
    if not meal_type:
        raise ValueError("meal_type is required")
    if not quantity:
        raise ValueError("quantity is required")

    quantity_details = _parse_quantity_details(quantity)
    normalized = _normalize_quantity(quantity_details)
    normalized_amount = normalized["amount"]
    normalized_unit = normalized["unit"]
    quantity_display_text = _format_quantity(normalized_amount, normalized_unit)

    estimated_calories_raw = meal_data.get("estimated_calories")
    if estimated_calories_raw is None:
        raise ValueError("estimated_calories is required")
    estimated_calories = float(str(estimated_calories_raw).replace(",", "."))
    if estimated_calories <= 0:
        raise ValueError("estimated_calories must be greater than zero")
    calorie_estimation = {
        "estimated_calories": round(estimated_calories, 2),
        "method": "llm_estimate",
        "quantity_details": quantity_details,
        "normalized_amount": normalized_amount,
        "normalized_unit": normalized_unit,
        "quantity_display_text": quantity_display_text,
    }
    estimated_calories = calorie_estimation["estimated_calories"]

    food_property, _ = _find_property_name(schema_properties, ("Alimento", "Food", "Nome", "Name"), {"title"})
    meal_property, meal_type_property = _find_property_name(
        schema_properties,
        ("Refeição", "Refeicao", "Meal", "Tipo de refeicao", "Tipo de refeição"),
        {"select", "multi_select", "rich_text", "title"},
    )
    quantity_property, quantity_type = _find_property_name(
        schema_properties,
        ("Quantidade", "Quantity"),
        {"number", "rich_text", "title"},
    )
    calories_property, calories_type = _find_property_name(
        schema_properties,
        ("Calorias", "Calories", "Kcal", "kcal"),
        {"number", "rich_text"},
    )
    date_property, date_property_type = _find_property_name(
        schema_properties,
        ("Data", "Date"),
        {"date", "rich_text", "title"},
    )

    if not food_property:
        raise ValueError("Meal food property not found in meals database")
    if not meal_property:
        raise ValueError("Meal type property not found in meals database")
    if not quantity_property:
        raise ValueError("Meal quantity property not found in meals database")
    if not calories_property:
        raise ValueError("Meal calories property not found in meals database")
    if not date_property:
        raise ValueError("Meal date property not found in meals database")

    properties = {
        food_property: {"title": [{"text": {"content": food_name}}]},
    }
    if meal_type_property == "multi_select":
        properties[meal_property] = {"multi_select": [{"name": meal_type}]}
    elif meal_type_property == "select":
        properties[meal_property] = {"select": {"name": meal_type}}
    elif meal_type_property == "title":
        properties[meal_property] = {"title": [{"text": {"content": meal_type}}]}
    else:
        properties[meal_property] = {"rich_text": _build_notion_rich_text_chunks(meal_type)}

    if quantity_type == "number":
        properties[quantity_property] = {"number": normalized_amount}
    elif quantity_type == "title":
        properties[quantity_property] = {"title": [{"text": {"content": quantity_display_text}}]}
    else:
        properties[quantity_property] = {"rich_text": _build_notion_rich_text_chunks(quantity_display_text)}

    if calories_type == "number":
        properties[calories_property] = {"number": float(estimated_calories)}
    else:
        properties[calories_property] = {"rich_text": _build_notion_rich_text_chunks(f"{estimated_calories:.2f}")}
    if date_property_type == "date":
        properties[date_property] = {"date": {"start": meal_date}}
    elif date_property_type == "title":
        properties[date_property] = {"title": [{"text": {"content": meal_date}}]}
    else:
        properties[date_property] = {"rich_text": _build_notion_rich_text_chunks(meal_date)}

    request_candidates = [
        {
            "notion_version": "2022-06-28",
            "parent": {"database_id": meals_database_id},
        },
        {
            "notion_version": os.getenv("NOTION_VERSION", "2025-09-03"),
            "parent": {"data_source_id": meals_database_id},
        },
    ]
    last_error = None
    for request_candidate in request_candidates:
        headers = {
            "accept": "application/json",
            "Authorization": "Bearer " + notion_credentials["api_key"] + "",
            "Notion-Version": request_candidate["notion_version"],
            "content-type": "application/json",
        }
        response = requests.post(
            "https://api.notion.com/v1/pages",
            json={"parent": request_candidate["parent"], "properties": properties},
            headers=headers,
            timeout=_NOTION_TIMEOUT,
        )
        if response.status_code in (400, 404):
            response_code = response.json().get("code", "")
            if response_code in ("validation_error", "object_not_found", "invalid_request"):
                last_error = response
                continue
        response.raise_for_status()
        payload = response.json()
        return {
            "id": payload.get("id"),
            "page_url": payload.get("url"),
            "food": food_name,
            "meal_type": meal_type,
            "quantity": quantity_display_text,
            "normalized_amount": normalized_amount,
            "normalized_unit": normalized_unit,
            "date": meal_date,
            "calories": estimated_calories,
            "calorie_estimation_method": calorie_estimation["method"],
        }

    if last_error is not None:
        last_error.raise_for_status()
    raise RuntimeError("Failed to create meal in Notion")


def collect_meals_from_database(*, start_datetime, end_datetime, project_logger=None, user_id=None, credential_store=None):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_meals_db_id.")
    meals_database_id = _get_meals_database_id(project_logger, user_id=user_id, store=credential_store)

    start_candidate = str(start_datetime or "").strip()
    end_candidate = str(end_datetime or "").strip()
    if not start_candidate:
        raise ValueError("start_datetime is required")
    if not end_candidate:
        raise ValueError("end_datetime is required")
    parsed_start = datetime.datetime.fromisoformat(start_candidate.replace("Z", "+00:00"))
    parsed_end = datetime.datetime.fromisoformat(end_candidate.replace("Z", "+00:00"))
    start_date = parsed_start.date().isoformat()
    end_date = parsed_end.date().isoformat()

    query_candidates = [
        {
            "url": f"https://api.notion.com/v1/data_sources/{meals_database_id}/query",
            "notion_version": os.getenv("NOTION_VERSION", "2025-09-03"),
            "filter_type": "date",
            "date_property": "Data",
        },
        {
            "url": f"https://api.notion.com/v1/databases/{meals_database_id}/query",
            "notion_version": "2022-06-28",
            "filter_type": "date",
            "date_property": "Data",
        },
        {
            "url": f"https://api.notion.com/v1/databases/{meals_database_id}/query",
            "notion_version": "2022-06-28",
            "filter_type": "date",
            "date_property": "Date",
        },
        {
            "url": f"https://api.notion.com/v1/databases/{meals_database_id}/query",
            "notion_version": "2022-06-28",
            "filter_type": "created_time",
        },
    ]

    all_meals = []
    selected_candidate = None
    has_more = True
    next_cursor = None
    while has_more:
        def _build_query_payload(candidate):
            if candidate.get("filter_type") == "date":
                payload = {
                    "filter": {
                        "and": [
                            {"property": candidate["date_property"], "date": {"on_or_after": start_date}},
                            {"property": candidate["date_property"], "date": {"on_or_before": end_date}},
                        ]
                    },
                    "sorts": [{"property": candidate["date_property"], "direction": "ascending"}],
                    "page_size": 100,
                }
            else:
                payload = {
                    "filter": {
                        "and": [
                            {"timestamp": "created_time", "created_time": {"on_or_after": start_candidate}},
                            {"timestamp": "created_time", "created_time": {"on_or_before": end_candidate}},
                        ]
                    },
                    "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
                    "page_size": 100,
                }
            if next_cursor:
                payload["start_cursor"] = next_cursor
            return payload

        if selected_candidate is None:
            last_error = None
            for candidate in query_candidates:
                request_payload = _build_query_payload(candidate)
                headers = {
                    "accept": "application/json",
                    "Authorization": "Bearer " + notion_credentials["api_key"] + "",
                    "Notion-Version": candidate["notion_version"],
                    "content-type": "application/json",
                }
                response = requests.post(candidate["url"], json=request_payload, headers=headers, timeout=_NOTION_TIMEOUT)
                if response.status_code in (400, 404):
                    response_code = response.json().get("code", "")
                    if response_code in ("validation_error", "invalid_request_url", "object_not_found"):
                        last_error = response
                        continue
                response.raise_for_status()
                selected_candidate = candidate
                break
            if selected_candidate is None:
                last_error.raise_for_status()
        else:
            request_payload = _build_query_payload(selected_candidate)
            headers = {
                "accept": "application/json",
                "Authorization": "Bearer " + notion_credentials["api_key"] + "",
                "Notion-Version": selected_candidate["notion_version"],
                "content-type": "application/json",
            }
            response = requests.post(
                selected_candidate["url"],
                json=request_payload,
                headers=headers,
                timeout=_NOTION_TIMEOUT,
            )
            response.raise_for_status()

        payload = response.json()
        for meal_page in payload.get("results", []):
            properties = meal_page.get("properties", {})
            food_title = (
                properties.get("Alimento", {}).get("title", [])
                or properties.get("Food", {}).get("title", [])
                or properties.get("Nome", {}).get("title", [])
                or properties.get("Name", {}).get("title", [])
            )
            if not food_title:
                continue

            meal_property = (
                properties.get("Refeição")
                or properties.get("Refeicao")
                or properties.get("Meal")
                or {}
            )
            quantity_property = (
                properties.get("Quantidade")
                or properties.get("Quantity")
                or {}
            )
            calories_property = (
                properties.get("Calorias")
                or properties.get("Calories")
                or properties.get("Kcal")
                or properties.get("kcal")
                or {}
            )

            calories = 0.0
            if calories_property.get("type") == "number":
                calories = float(calories_property.get("number") or 0.0)
            elif calories_property.get("type") == "rich_text":
                parsed = _extract_first_float(_extract_plain_text(calories_property))
                calories = float(parsed or 0.0)

            quantity_value = ""
            if quantity_property.get("type") == "number":
                quantity_value = str(quantity_property.get("number"))
            elif quantity_property.get("type") in {"rich_text", "title"}:
                quantity_value = _extract_plain_text(quantity_property)

            meal_date_payload = (
                properties.get("Data", {}).get("date")
                or properties.get("Date", {}).get("date")
                or {}
            )
            meal_date = str(meal_date_payload.get("start") or "").strip()
            if not meal_date:
                meal_date = str(meal_page.get("created_time") or "")[:10]

            all_meals.append(
                {
                    "id": meal_page.get("id"),
                    "food": food_title[0].get("plain_text") or food_title[0].get("text", {}).get("content"),
                    "meal_type": _extract_select_name(meal_property) or "Não informado",
                    "quantity": quantity_value,
                    "date": meal_date,
                    "calories": round(calories, 2),
                    "created_time": meal_page.get("created_time"),
                    "page_url": meal_page.get("url"),
                }
            )

        has_more = payload.get("has_more", False)
        next_cursor = payload.get("next_cursor")

    return sorted(
        all_meals,
        key=lambda item: (
            item.get("date") or "",
            item.get("created_time") or "",
        ),
    )


def create_exercise_in_exercises_db(exercise_data, project_logger=None, user_id=None, credential_store=None):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_exercises_db_id.")
    exercises_database_id = _get_exercises_database_id(project_logger, user_id=user_id, store=credential_store)
    schema_properties = _fetch_database_schema(exercises_database_id, notion_credentials["api_key"])

    activity = str(exercise_data.get("activity", "")).strip()
    if not activity:
        raise ValueError("activity is required")

    raw_calories = exercise_data.get("calories")
    if raw_calories is None:
        raise ValueError("calories is required")
    calories = float(str(raw_calories).replace(",", "."))
    if calories <= 0:
        raise ValueError("calories must be greater than zero")

    exercise_date = str(exercise_data.get("date", today_iso_in_configured_timezone())).strip()
    exercise_date_value = datetime.date.fromisoformat(exercise_date)
    observations = str(exercise_data.get("observations", "")).strip()
    raw_done = exercise_data.get("done")
    if raw_done is None:
        done = exercise_date_value <= today_in_configured_timezone()
    else:
        done = _coerce_boolean_value(raw_done, field_name="done")

    activity_property, activity_type = _find_property_name(
        schema_properties,
        ("Atividade", "Activity", "Nome", "Name"),
        {"title", "rich_text"},
    )
    date_property, date_type = _find_property_name(
        schema_properties,
        ("Data", "Date"),
        {"date"},
    )
    calories_property, calories_type = _find_property_name(
        schema_properties,
        ("Calorias", "Calories", "Kcal", "kcal"),
        {"number", "rich_text"},
    )
    observations_property, observations_type = _find_property_name(
        schema_properties,
        ("Observações", "Observacoes", "Description"),
        {"rich_text"},
    )
    done_property, done_type = _find_property_name(
        schema_properties,
        ("Done", "Concluído", "Concluido", "Finalizado"),
        {"checkbox"},
    )

    if not activity_property:
        raise ValueError("Exercise activity property not found in exercises database")
    if not date_property:
        raise ValueError("Exercise date property not found in exercises database")
    if not calories_property:
        raise ValueError("Exercise calories property not found in exercises database")

    properties = {}
    if activity_type == "title":
        properties[activity_property] = {"title": [{"text": {"content": activity}}]}
    else:
        properties[activity_property] = {"rich_text": _build_notion_rich_text_chunks(activity)}

    if date_type == "date":
        properties[date_property] = {"date": {"start": exercise_date}}
    if calories_type == "number":
        properties[calories_property] = {"number": calories}
    else:
        properties[calories_property] = {"rich_text": _build_notion_rich_text_chunks(f"{calories:.2f}")}
    if observations and observations_property and observations_type == "rich_text":
        properties[observations_property] = {"rich_text": _build_notion_rich_text_chunks(observations)}
    if done_property and done_type == "checkbox":
        properties[done_property] = {"checkbox": done}

    request_candidates = [
        {
            "notion_version": "2022-06-28",
            "parent": {"database_id": exercises_database_id},
        },
        {
            "notion_version": os.getenv("NOTION_VERSION", "2025-09-03"),
            "parent": {"data_source_id": exercises_database_id},
        },
    ]
    last_error = None
    for request_candidate in request_candidates:
        headers = {
            "accept": "application/json",
            "Authorization": "Bearer " + notion_credentials["api_key"] + "",
            "Notion-Version": request_candidate["notion_version"],
            "content-type": "application/json",
        }
        response = requests.post(
            "https://api.notion.com/v1/pages",
            json={"parent": request_candidate["parent"], "properties": properties},
            headers=headers,
            timeout=_NOTION_TIMEOUT,
        )
        if response.status_code in (400, 404):
            response_code = response.json().get("code", "")
            if response_code in ("validation_error", "object_not_found", "invalid_request"):
                last_error = response
                continue
        response.raise_for_status()
        payload = response.json()
        return {
            "id": payload.get("id"),
            "page_url": payload.get("url"),
            "activity": activity,
            "date": exercise_date,
            "calories": round(calories, 2),
            "observations": observations,
            "done": done,
        }

    if last_error is not None:
        last_error.raise_for_status()
    raise RuntimeError("Failed to create exercise in Notion")


def update_exercise_in_exercises_db(
    page_id,
    *,
    activity=None,
    date=None,
    calories=None,
    observations=None,
    done=None,
    project_logger=None,
    user_id=None,
    credential_store=None,
):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_exercises_db_id.")
    exercises_database_id = _get_exercises_database_id(project_logger, user_id=user_id, store=credential_store)
    schema_properties = _fetch_database_schema(exercises_database_id, notion_credentials["api_key"])

    normalized_page_id = _normalize_notion_object_id(page_id)
    if not normalized_page_id:
        raise ValueError("page_id is required")

    has_updates = any(value is not None for value in (activity, date, calories, observations, done))
    if not has_updates:
        raise ValueError("At least one field is required to update")

    properties = {}
    updated_fields = []

    if activity is not None:
        clean_activity = str(activity).strip()
        if not clean_activity:
            raise ValueError("activity must be a non-empty string")
        activity_property, activity_type = _find_property_name(
            schema_properties,
            ("Atividade", "Activity", "Nome", "Name"),
            {"title", "rich_text"},
        )
        if not activity_property:
            raise ValueError("Exercise activity property not found in exercises database")
        if activity_type == "title":
            properties[activity_property] = {"title": [{"text": {"content": clean_activity}}]}
        else:
            properties[activity_property] = {"rich_text": _build_notion_rich_text_chunks(clean_activity)}
        updated_fields.append("activity")

    if date is not None:
        clean_date = str(date).strip()
        datetime.date.fromisoformat(clean_date)
        date_property, _ = _find_property_name(
            schema_properties,
            ("Data", "Date"),
            {"date"},
        )
        if not date_property:
            raise ValueError("Exercise date property not found in exercises database")
        properties[date_property] = {"date": {"start": clean_date}}
        updated_fields.append("date")

    if calories is not None:
        clean_calories = float(str(calories).replace(",", "."))
        if clean_calories <= 0:
            raise ValueError("calories must be greater than zero")
        calories_property, calories_type = _find_property_name(
            schema_properties,
            ("Calorias", "Calories", "Kcal", "kcal"),
            {"number", "rich_text"},
        )
        if not calories_property:
            raise ValueError("Exercise calories property not found in exercises database")
        if calories_type == "number":
            properties[calories_property] = {"number": clean_calories}
        else:
            properties[calories_property] = {"rich_text": _build_notion_rich_text_chunks(f"{clean_calories:.2f}")}
        updated_fields.append("calories")

    if observations is not None:
        observations_property, _ = _find_property_name(
            schema_properties,
            ("Observações", "Observacoes", "Description"),
            {"rich_text"},
        )
        if not observations_property:
            raise ValueError("Exercise observations property not found in exercises database")
        clean_observations = str(observations).strip()
        properties[observations_property] = {
            "rich_text": _build_notion_rich_text_chunks(clean_observations),
        }
        updated_fields.append("observations")

    if done is not None:
        done_property, done_type = _find_property_name(
            schema_properties,
            ("Done", "Concluído", "Concluido", "Finalizado"),
            {"checkbox"},
        )
        if not done_property or done_type != "checkbox":
            raise ValueError("Exercise done property not found in exercises database")
        clean_done = _coerce_boolean_value(done, field_name="done")
        properties[done_property] = {"checkbox": clean_done}
        updated_fields.append("done")

    headers = {
        "accept": "application/json",
        "Authorization": "Bearer " + notion_credentials["api_key"] + "",
        "Notion-Version": "2022-06-28",
        "content-type": "application/json",
    }
    response = requests.patch(
        f"https://api.notion.com/v1/pages/{normalized_page_id}",
        json={"properties": properties},
        headers=headers,
        timeout=_NOTION_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    return {
        "id": payload.get("id"),
        "page_url": payload.get("url"),
        "updated_fields": updated_fields,
    }


def collect_exercises_from_database(*, start_datetime, end_datetime, project_logger=None, user_id=None, credential_store=None):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_exercises_db_id.")
    exercises_database_id = _get_exercises_database_id(project_logger, user_id=user_id, store=credential_store)

    start_candidate = str(start_datetime or "").strip()
    end_candidate = str(end_datetime or "").strip()
    if not start_candidate:
        raise ValueError("start_datetime is required")
    if not end_candidate:
        raise ValueError("end_datetime is required")

    parsed_start = datetime.datetime.fromisoformat(start_candidate.replace("Z", "+00:00"))
    parsed_end = datetime.datetime.fromisoformat(end_candidate.replace("Z", "+00:00"))
    start_date = parsed_start.date().isoformat()
    end_date = parsed_end.date().isoformat()

    query_candidates = [
        {
            "url": f"https://api.notion.com/v1/data_sources/{exercises_database_id}/query",
            "notion_version": os.getenv("NOTION_VERSION", "2025-09-03"),
            "filter_type": "date",
            "date_property": "Data",
        },
        {
            "url": f"https://api.notion.com/v1/databases/{exercises_database_id}/query",
            "notion_version": "2022-06-28",
            "filter_type": "date",
            "date_property": "Data",
        },
        {
            "url": f"https://api.notion.com/v1/databases/{exercises_database_id}/query",
            "notion_version": "2022-06-28",
            "filter_type": "date",
            "date_property": "Date",
        },
        {
            "url": f"https://api.notion.com/v1/databases/{exercises_database_id}/query",
            "notion_version": "2022-06-28",
            "filter_type": "created_time",
        },
    ]

    all_exercises = []
    selected_candidate = None
    has_more = True
    next_cursor = None
    while has_more:
        def _build_query_payload(candidate):
            if candidate.get("filter_type") == "date":
                payload = {
                    "filter": {
                        "and": [
                            {"property": candidate["date_property"], "date": {"on_or_after": start_date}},
                            {"property": candidate["date_property"], "date": {"on_or_before": end_date}},
                        ]
                    },
                    "sorts": [{"property": candidate["date_property"], "direction": "ascending"}],
                    "page_size": 100,
                }
            else:
                payload = {
                    "filter": {
                        "and": [
                            {"timestamp": "created_time", "created_time": {"on_or_after": start_candidate}},
                            {"timestamp": "created_time", "created_time": {"on_or_before": end_candidate}},
                        ]
                    },
                    "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
                    "page_size": 100,
                }
            if next_cursor:
                payload["start_cursor"] = next_cursor
            return payload

        if selected_candidate is None:
            last_error = None
            for candidate in query_candidates:
                request_payload = _build_query_payload(candidate)
                headers = {
                    "accept": "application/json",
                    "Authorization": "Bearer " + notion_credentials["api_key"] + "",
                    "Notion-Version": candidate["notion_version"],
                    "content-type": "application/json",
                }
                response = requests.post(candidate["url"], json=request_payload, headers=headers, timeout=_NOTION_TIMEOUT)
                if response.status_code in (400, 404):
                    response_code = response.json().get("code", "")
                    if response_code in ("validation_error", "invalid_request_url", "object_not_found"):
                        last_error = response
                        continue
                response.raise_for_status()
                selected_candidate = candidate
                break
            if selected_candidate is None:
                last_error.raise_for_status()
        else:
            request_payload = _build_query_payload(selected_candidate)
            headers = {
                "accept": "application/json",
                "Authorization": "Bearer " + notion_credentials["api_key"] + "",
                "Notion-Version": selected_candidate["notion_version"],
                "content-type": "application/json",
            }
            response = requests.post(
                selected_candidate["url"],
                json=request_payload,
                headers=headers,
                timeout=_NOTION_TIMEOUT,
            )
            response.raise_for_status()

        payload = response.json()
        for exercise_page in payload.get("results", []):
            properties = exercise_page.get("properties", {})
            activity_property = (
                properties.get("Atividade")
                or properties.get("Activity")
                or properties.get("Nome")
                or properties.get("Name")
                or {}
            )
            activity = _extract_plain_text(activity_property)
            if not activity:
                continue

            calories_property = (
                properties.get("Calorias")
                or properties.get("Calories")
                or properties.get("Kcal")
                or properties.get("kcal")
                or {}
            )
            calories = 0.0
            if calories_property.get("type") == "number":
                calories = float(calories_property.get("number") or 0.0)
            elif calories_property.get("type") == "rich_text":
                calories_value = _extract_first_float(_extract_plain_text(calories_property))
                calories = float(calories_value or 0.0)

            exercise_date_payload = (
                properties.get("Data", {}).get("date")
                or properties.get("Date", {}).get("date")
                or {}
            )
            exercise_date = str(exercise_date_payload.get("start") or "").strip()
            if not exercise_date:
                exercise_date = str(exercise_page.get("created_time") or "")[:10]

            observations_property = (
                properties.get("Observações")
                or properties.get("Observacoes")
                or properties.get("Description")
                or {}
            )
            observations = _extract_plain_text(observations_property)
            done_property = (
                properties.get("Done")
                or properties.get("Concluído")
                or properties.get("Concluido")
                or properties.get("Finalizado")
                or {}
            )
            done_value = done_property.get("checkbox") if done_property.get("type") == "checkbox" else None

            all_exercises.append(
                {
                    "id": exercise_page.get("id"),
                    "activity": activity,
                    "date": exercise_date,
                    "calories": round(calories, 2),
                    "observations": observations,
                    "done": done_value,
                    "created_time": exercise_page.get("created_time"),
                    "page_url": exercise_page.get("url"),
                }
            )

        has_more = payload.get("has_more", False)
        next_cursor = payload.get("next_cursor")

    return sorted(
        all_exercises,
        key=lambda item: (
            item.get("date") or "",
            item.get("created_time") or "",
        ),
    )


def _extract_plain_text(property_payload):
    payload_type = property_payload.get("type")
    if payload_type == "rich_text":
        source = property_payload.get("rich_text", [])
    elif payload_type == "title":
        source = property_payload.get("title", [])
    else:
        source = []
    return "".join(
        chunk.get("plain_text") or chunk.get("text", {}).get("content", "")
        for chunk in source
    ).strip()


def _extract_select_name(property_payload):
    payload_type = property_payload.get("type")
    if payload_type == "select":
        return str(property_payload.get("select", {}).get("name") or "").strip()
    if payload_type == "multi_select":
        first_item = property_payload.get("multi_select", [])
        if first_item:
            return str(first_item[0].get("name") or "").strip()
    if payload_type in {"rich_text", "title"}:
        return _extract_plain_text(property_payload)
    return ""


def _coerce_boolean_value(raw_value, *, field_name):
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)) and raw_value in {0, 1}:
        return bool(int(raw_value))

    normalized = str(raw_value or "").strip().lower()
    truthy_values = {"1", "true", "t", "yes", "y", "sim", "s"}
    falsy_values = {"0", "false", "f", "no", "n", "nao", "não"}
    if normalized in truthy_values:
        return True
    if normalized in falsy_values:
        return False
    raise ValueError(f"{field_name} must be a boolean")


def collect_monthly_bills_from_database(*, start_date, end_date, unpaid_only=False, project_logger=None, user_id=None, credential_store=None):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_monthly_bills_db_id.")
    monthly_bills_database_id = _get_monthly_bills_database_id(project_logger, user_id=user_id, store=credential_store)

    query_candidates = [
        {
            "url": f"https://api.notion.com/v1/data_sources/{monthly_bills_database_id}/query",
            "notion_version": os.getenv("NOTION_VERSION", "2025-09-03"),
            "date_property": "Data",
            "paid_property": "Pago",
        },
        {
            "url": f"https://api.notion.com/v1/databases/{monthly_bills_database_id}/query",
            "notion_version": "2022-06-28",
            "date_property": "Data",
            "paid_property": "Pago",
        },
    ]

    all_bills = []
    selected_candidate = None
    has_more = True
    next_cursor = None
    while has_more:
        if selected_candidate is None:
            last_error = None
            for candidate in query_candidates:
                filters = [
                    {"property": candidate["date_property"], "date": {"on_or_after": start_date}},
                    {"property": candidate["date_property"], "date": {"on_or_before": end_date}},
                ]
                if unpaid_only:
                    filters.append({"property": candidate["paid_property"], "checkbox": {"equals": False}})
                request_payload = {
                    "filter": {"and": filters},
                    "sorts": [{"property": candidate["date_property"], "direction": "ascending"}],
                    "page_size": 100,
                }
                if next_cursor:
                    request_payload["start_cursor"] = next_cursor
                headers = {
                    "accept": "application/json",
                    "Authorization": "Bearer " + notion_credentials["api_key"] + "",
                    "Notion-Version": candidate["notion_version"],
                    "content-type": "application/json",
                }
                response = requests.post(candidate["url"], json=request_payload, headers=headers, timeout=_NOTION_TIMEOUT)
                if response.status_code in (400, 404):
                    response_code = response.json().get("code", "")
                    if response_code in ("validation_error", "invalid_request_url", "object_not_found"):
                        last_error = response
                        continue
                response.raise_for_status()
                selected_candidate = candidate
                break
            if selected_candidate is None:
                last_error.raise_for_status()
        else:
            filters = [
                {"property": selected_candidate["date_property"], "date": {"on_or_after": start_date}},
                {"property": selected_candidate["date_property"], "date": {"on_or_before": end_date}},
            ]
            if unpaid_only:
                filters.append({"property": selected_candidate["paid_property"], "checkbox": {"equals": False}})
            request_payload = {
                "filter": {"and": filters},
                "sorts": [{"property": selected_candidate["date_property"], "direction": "ascending"}],
                "page_size": 100,
            }
            if next_cursor:
                request_payload["start_cursor"] = next_cursor
            headers = {
                "accept": "application/json",
                "Authorization": "Bearer " + notion_credentials["api_key"] + "",
                "Notion-Version": selected_candidate["notion_version"],
                "content-type": "application/json",
            }
            response = requests.post(
                selected_candidate["url"],
                json=request_payload,
                headers=headers,
                timeout=_NOTION_TIMEOUT,
            )
            response.raise_for_status()

        payload = response.json()
        for bill_page in payload.get("results", []):
            properties = bill_page.get("properties", {})
            bill_name = (
                properties.get("Nome", {}).get("title", [])
                or properties.get("Name", {}).get("title", [])
            )
            bill_date = (
                properties.get("Data", {}).get("date")
                or properties.get("Date", {}).get("date")
            )
            if not bill_name or not bill_date or not bill_date.get("start"):
                continue

            paid_property = properties.get("Pago", {})
            budget_property = properties.get("Budget") or properties.get("Orçamento") or properties.get("Orcamento") or {}
            paid_amount_property = properties.get("Valor pago") or properties.get("Valor Pago") or properties.get("Paid Amount") or {}
            description_property = properties.get("Descrição") or properties.get("Descricao") or properties.get("Description") or {}
            category_property = properties.get("Categoria") or properties.get("Category") or {}

            budget = 0.0
            if budget_property.get("type") == "number":
                budget = float(budget_property.get("number") or 0.0)
            paid_amount = 0.0
            if paid_amount_property.get("type") == "number":
                paid_amount = float(paid_amount_property.get("number") or 0.0)

            all_bills.append(
                {
                    "id": bill_page.get("id"),
                    "name": bill_name[0].get("plain_text") or bill_name[0].get("text", {}).get("content"),
                    "date": bill_date.get("start"),
                    "paid": bool(paid_property.get("checkbox", False)),
                    "category": _extract_select_name(category_property) or "Sem categoria",
                    "budget": round(budget, 2),
                    "paid_amount": round(paid_amount, 2),
                    "description": _extract_plain_text(description_property),
                    "page_url": bill_page.get("url"),
                }
            )

        has_more = payload.get("has_more", False)
        next_cursor = payload.get("next_cursor")

    return all_bills


def update_monthly_bill_payment(
    *,
    page_id,
    paid,
    paid_amount=None,
    payment_date=None,
    project_logger=None,
    user_id=None,
    credential_store=None,
):
    project_logger = project_logger or logging.getLogger(__name__)
    notion_credentials = load_credentials.load_notion_credentials(project_logger=project_logger, user_id=user_id, store=credential_store)
    if not notion_credentials:
        raise ValueError("Notion integration not configured. Please set notion_api_key and notion_monthly_bills_db_id.")
    normalized_page_id = _normalize_notion_object_id(page_id)
    if not normalized_page_id:
        raise ValueError("page_id is required")

    properties = {"Pago": {"checkbox": bool(paid)}}
    if paid_amount is not None:
        properties["Valor pago"] = {"number": float(paid_amount)}
    if payment_date:
        datetime.date.fromisoformat(str(payment_date))
        properties["Data"] = {"date": {"start": str(payment_date)}}

    headers = {
        "accept": "application/json",
        "Authorization": "Bearer " + notion_credentials["api_key"] + "",
        "Notion-Version": "2022-06-28",
        "content-type": "application/json",
    }
    response = requests.patch(
        f"https://api.notion.com/v1/pages/{normalized_page_id}",
        json={"properties": properties},
        headers=headers,
        timeout=_NOTION_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    return {
        "id": payload.get("id"),
        "page_url": payload.get("url"),
        "paid": bool(paid),
        "paid_amount": round(float(paid_amount), 2) if paid_amount is not None else None,
        "payment_date": str(payment_date) if payment_date else None,
    }
