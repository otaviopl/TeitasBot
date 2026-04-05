import datetime
import json
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from utils.timezone_utils import get_configured_timezone, now_in_configured_timezone, today_in_configured_timezone


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def calendar_connect(project_logger, user_id=None, credential_store=None):
    """
    Create a Google Calendar service object.

    Credential resolution order:
    1. Per-user token stored in credential_store (if user_id + store provided)
    2. System-level token.json file
    3. ValueError — interactive browser flow is NOT supported (headless server)
    """
    creds = None
    _from_store = False

    project_logger.debug("Connecting Google Calendar OAuth2...")

    if credential_store is not None and user_id is not None:
        raw = credential_store.get_credential(str(user_id), "google_token_json", use_env_fallback=False)
        if raw:
            try:
                creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
                _from_store = True
            except Exception:
                project_logger.warning("Failed to parse stored Google token for user %s", user_id)
                creds = None

    if creds is None and os.path.exists("token.json"):
        creds = _load_credentials_from_token("token.json", SCOPES, project_logger)

    if not creds:
        raise ValueError(
            "Google não autorizado. Autorize sua conta Google para usar o Calendar."
        )

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            if _from_store and credential_store is not None and user_id is not None:
                credential_store.set_credential(str(user_id), "google_token_json", creds.to_json())
            else:
                with open("token.json", "w", encoding="utf-8") as token:
                    token.write(creds.to_json())
        else:
            raise ValueError(
                "Token do Google inválido ou expirado. Autorize novamente sua conta Google."
            )

    return build("calendar", "v3", credentials=creds)


def _load_credentials_from_token(token_path, scopes, project_logger):
    try:
        return Credentials.from_authorized_user_file(token_path, scopes)
    except json.JSONDecodeError:
        project_logger.warning("token.json has trailing data; attempting auto-recovery.")
        with open(token_path, "r", encoding="utf-8") as token_file:
            token_payload = _extract_first_json_object(token_file.read())
        with open(token_path, "w", encoding="utf-8") as token_file:
            json.dump(token_payload, token_file)
        return Credentials.from_authorized_user_info(token_payload, scopes)


def _extract_first_json_object(content):
    decoder = json.JSONDecoder()
    payload, _ = decoder.raw_decode(content.lstrip())
    if not isinstance(payload, dict):
        raise ValueError("Invalid token payload format")
    return payload


def _to_utc_rfc3339(value):
    return value.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def list_upcoming_events(project_logger, max_results=10, user_id=None, credential_store=None):
    service = calendar_connect(project_logger=project_logger, user_id=user_id, credential_store=credential_store)
    now = _to_utc_rfc3339(now_in_configured_timezone())

    response = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = []
    for event in response.get("items", []):
        start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
        events.append(
            {
                "id": event.get("id"),
                "summary": event.get("summary", "Sem título"),
                "start": start,
                "html_link": event.get("htmlLink"),
            }
        )
    return events


def list_week_events(project_logger, max_results=100, user_id=None, credential_store=None):
    service = calendar_connect(project_logger=project_logger, user_id=user_id, credential_store=credential_store)
    now = now_in_configured_timezone()
    week_end = now + datetime.timedelta(days=7)

    response = service.events().list(
        calendarId="primary",
        timeMin=_to_utc_rfc3339(now),
        timeMax=_to_utc_rfc3339(week_end),
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = []
    for event in response.get("items", []):
        start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
        end = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
        events.append(
            {
                "id": event.get("id"),
                "summary": event.get("summary", "Sem título"),
                "start": start,
                "end": end,
                "html_link": event.get("htmlLink"),
                "location": event.get("location"),
            }
        )
    return events


def list_current_week_events(project_logger, max_results=100, user_id=None, credential_store=None):
    service = calendar_connect(project_logger=project_logger, user_id=user_id, credential_store=credential_store)
    today = today_in_configured_timezone()
    days_since_sunday = (today.weekday() + 1) % 7
    week_start_date = today - datetime.timedelta(days=days_since_sunday)
    week_start = datetime.datetime.combine(
        week_start_date,
        datetime.time.min,
        tzinfo=get_configured_timezone(),
    )
    next_week_start = week_start + datetime.timedelta(days=7)

    response = service.events().list(
        calendarId="primary",
        timeMin=_to_utc_rfc3339(week_start),
        timeMax=_to_utc_rfc3339(next_week_start),
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = []
    for event in response.get("items", []):
        start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
        end = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
        events.append(
            {
                "id": event.get("id"),
                "summary": event.get("summary", "Sem título"),
                "start": start,
                "end": end,
                "html_link": event.get("htmlLink"),
                "location": event.get("location"),
            }
        )
    return events


def create_calendar_event(
    project_logger,
    summary,
    start_datetime,
    end_datetime,
    description=None,
    timezone="UTC",
    attendees=None,
    user_id=None,
    credential_store=None,
):
    service = calendar_connect(project_logger=project_logger, user_id=user_id, credential_store=credential_store)
    start_rfc3339, start_dt = _normalize_event_datetime(start_datetime, timezone)
    end_rfc3339, end_dt = _normalize_event_datetime(end_datetime, timezone)
    if end_dt <= start_dt:
        raise ValueError("end_datetime must be after start_datetime")

    event_body = {
        "summary": summary,
        "start": {"dateTime": start_rfc3339, "timeZone": timezone},
        "end": {"dateTime": end_rfc3339, "timeZone": timezone},
        "conferenceData": {
            "createRequest": {
                "requestId": f"{summary}-{start_rfc3339}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    if description:
        event_body["description"] = description
    if attendees:
        if isinstance(attendees, str):
            attendees = [e.strip() for e in attendees.split(",") if e.strip()]
        event_body["attendees"] = [{"email": email} for email in attendees]

    send_updates = "all" if attendees else "none"
    created_event = (
        service.events()
        .insert(calendarId="primary", body=event_body, conferenceDataVersion=1, sendUpdates=send_updates)
        .execute()
    )
    meet_link = None
    conference = created_event.get("conferenceData", {})
    for ep in conference.get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            meet_link = ep.get("uri")
            break

    return {
        "id": created_event.get("id"),
        "summary": created_event.get("summary"),
        "start": created_event.get("start", {}).get("dateTime"),
        "end": created_event.get("end", {}).get("dateTime"),
        "html_link": created_event.get("htmlLink"),
        "meet_link": meet_link,
    }


def _normalize_event_datetime(value, timezone):
    tz = _get_timezone(timezone)
    text = str(value).strip()
    if not text:
        raise ValueError("Event datetime is required")

    parsed = None
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for date_format in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.datetime.strptime(text, date_format)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError("Invalid event datetime format. Use YYYY-MM-DDTHH:MM")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)
    return parsed.isoformat(), parsed


def _get_timezone(timezone):
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Invalid timezone: {timezone}") from error
