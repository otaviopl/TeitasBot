from __future__ import annotations

from calendar_connector import calendar_connector


def list_calendar_events(arguments, context):
    try:
        max_results = int(arguments.get("max_results", 20))
    except (ValueError, TypeError):
        raise ValueError("max_results must be a valid integer")
    max_results = min(max(max_results, 1), 100)

    events = calendar_connector.list_week_events(
        project_logger=context.project_logger,
        max_results=max_results,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    return {
        "total": len(events),
        "events": events,
    }


def create_calendar_event(arguments, context):
    summary = str(arguments.get("summary", "")).strip()
    start_datetime = str(arguments.get("start_datetime", "")).strip()
    end_datetime = str(arguments.get("end_datetime", "")).strip()
    description = str(arguments.get("description", "")).strip()
    timezone = str(arguments.get("timezone", "America/Sao_Paulo")).strip() or "America/Sao_Paulo"
    attendees = arguments.get("attendees")

    if not summary:
        raise ValueError("summary is required")
    if not start_datetime:
        raise ValueError("start_datetime is required")
    if not end_datetime:
        raise ValueError("end_datetime is required")

    if isinstance(attendees, str):
        attendees = [e.strip() for e in attendees.split(",") if e.strip()] or None
    elif isinstance(attendees, list):
        attendees = [str(e).strip() for e in attendees if str(e).strip()] or None

    return calendar_connector.create_calendar_event(
        project_logger=context.project_logger,
        summary=summary,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        description=description,
        timezone=timezone,
        attendees=attendees,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
