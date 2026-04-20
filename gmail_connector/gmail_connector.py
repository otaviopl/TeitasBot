import os
import base64
import json
import io
import zipfile
import xml.etree.ElementTree as ET

from jinja2 import Environment, FileSystemLoader

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from email.mime.text import MIMEText

from utils import message_parser
from utils import nice_message_collector
from utils import load_credentials


SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/calendar.readonly',
        'https://www.googleapis.com/auth/calendar.events',
    ]


def gmail_connect(project_logger, user_id=None, credential_store=None):
    """
    Create a Google Gmail service object.

    Credential resolution order:
    1. Per-user token stored in credential_store (if user_id + store provided)
    2. System-level token.json file
    3. ValueError — interactive browser flow is NOT supported (headless server)
    """
    creds = None
    _from_store = False

    project_logger.debug("Connecting Gmail OAuth2...")

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
            "Google não autorizado. Autorize sua conta Google para usar Gmail."
        )

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            if _from_store and credential_store is not None and user_id is not None:
                credential_store.set_credential(str(user_id), "google_token_json", creds.to_json())
            else:
                with open("token.json", "w") as token:
                    token.write(creds.to_json())
        else:
            raise ValueError(
                "Token do Google inválido ou expirado. Autorize novamente sua conta Google."
            )

    return build("gmail", "v1", credentials=creds)


def build_email_body(all_tasks, display_name, chatgpt_answer,
                     project_logger, to_file=False):
    """
    Build the email HTML using Jinja2. Template is in template/ folder.
    """
    project_logger.info("Building email body...")

    message_json, general_message = message_parser.\
        parse_chatgpt_message(chatgpt_answer, project_logger)
    if message_json is None:
        return False

    nice_message = nice_message_collector.get_motivational_message(
        project_logger=project_logger
    )["text"]

    environment = Environment(loader=FileSystemLoader("templates/"))
    template = environment.get_template("email_template.html")
    context = {
        "username": display_name,
        "all_tasks": all_tasks,
        "nice_message": nice_message,
        "json_gpt_tasks": message_json,
        "gpt_general_comment": general_message
    }

    html_output = template.render(context)

    if to_file:
        with open("some_new_file.html", "w") as f:
            f.write(html_output)

    return html_output


def send_email_with_tasks(all_tasks, chatgpt_answer, project_logger,
                          fake_send=False, user_id=None, credential_store=None):
    """
    Send an email with task summary using Gmail API.
    """
    project_logger.info("Sending email...")
    email_config = load_credentials.load_email_config(
        project_logger=project_logger, user_id=user_id, store=credential_store
    )
    email_message = build_email_body(
        all_tasks,
        email_config["display_name"],
        chatgpt_answer,
        project_logger,
        to_file=fake_send
    )
    if not email_message:
        project_logger.error("Email body generation failed.")
        return None

    if fake_send:
        return True

    try:
        service = gmail_connect(project_logger, user_id=user_id, credential_store=credential_store)
        message = MIMEText(email_message, 'html')

        message['To'] = email_config["email_to"]
        message['From'] = email_config["email_from"]
        message['Subject'] = 'Personal Assistant - Tasks'

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()) \
            .decode()

        create_message = {
            'raw': encoded_message
        }

        send_message = (service.users().messages().send
                        (userId="me", body=create_message).execute())

    except HttpError as error:
        project_logger.error(F'An error occurred: {error}')
        send_message = None

    return send_message


def send_custom_email(
    project_logger,
    subject,
    body_text,
    email_to=None,
    email_from=None,
    body_subtype="plain",
    reply_to_message_id=None,
    fake_send=False,
    user_id=None,
    credential_store=None,
):
    """
    Send a custom email through Gmail API.
    """
    clean_subject = str(subject or "").strip()
    clean_body = str(body_text or "").strip()
    if not clean_subject:
        raise ValueError("Email subject is required")
    if not clean_body:
        raise ValueError("Email body is required")

    resolved_to = str(email_to or os.getenv("EMAIL_TO", "")).strip()
    resolved_from = str(email_from or os.getenv("EMAIL_FROM", "")).strip()
    if not resolved_to:
        raise ValueError("Destination email is required")
    if not resolved_from:
        raise ValueError("Source email is required")

    message = MIMEText(clean_body, body_subtype, "utf-8")
    message["To"] = resolved_to
    message["From"] = resolved_from
    message["Subject"] = clean_subject

    if fake_send:
        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        return {
            "to": resolved_to,
            "from": resolved_from,
            "subject": clean_subject,
            "raw": encoded_message,
            "reply_to_message_id": str(reply_to_message_id or "").strip() or None,
        }

    service = gmail_connect(project_logger, user_id=user_id, credential_store=credential_store)
    send_payload = {}
    clean_reply_to_message_id = str(reply_to_message_id or "").strip()
    if clean_reply_to_message_id:
        reply_metadata = _get_reply_metadata(
            service=service,
            message_id=clean_reply_to_message_id,
        )
        original_message_id = reply_metadata.get("message_id")
        if original_message_id:
            message["In-Reply-To"] = original_message_id
            references = " ".join(
                token
                for token in [reply_metadata.get("references", ""), original_message_id]
                if token
            ).strip()
            if references:
                message["References"] = references
        if reply_metadata.get("thread_id"):
            send_payload["threadId"] = reply_metadata["thread_id"]

    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    send_payload["raw"] = encoded_message
    sent = service.users().messages().send(userId="me", body=send_payload).execute()
    return {
        "id": sent.get("id"),
        "thread_id": sent.get("threadId"),
        "to": resolved_to,
        "from": resolved_from,
        "subject": clean_subject,
    }


def _get_reply_metadata(service, message_id):
    response = service.users().messages().get(
        userId="me",
        id=message_id,
        format="metadata",
        metadataHeaders=["Message-Id", "References"],
    ).execute()
    headers = _extract_headers(response.get("payload", {}) or {})
    return {
        "thread_id": response.get("threadId"),
        "message_id": headers.get("message-id", ""),
        "references": headers.get("references", ""),
    }


def search_emails(project_logger, query="", max_results=10, include_body=False, user_id=None, credential_store=None):
    """
    Search Gmail messages and return normalized metadata.
    """
    clean_query = str(query or "").strip()
    limit = max(1, min(int(max_results or 10), 50))
    service = _build_gmail_service(project_logger, user_id=user_id, credential_store=credential_store)
    search_kwargs = {"userId": "me", "maxResults": limit}
    if clean_query:
        search_kwargs["q"] = clean_query
    response = service.users().messages().list(**search_kwargs).execute()
    messages = response.get("messages", [])

    email_items = []
    for message_ref in messages:
        details = service.users().messages().get(
            userId="me",
            id=message_ref.get("id"),
            format="full" if include_body else "metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        email_items.append(_normalize_message_payload(details, include_body=include_body))

    return {
        "query": clean_query,
        "returned": len(email_items),
        "emails": email_items,
    }


def read_email(project_logger, message_id, include_body=True, user_id=None, credential_store=None):
    """
    Read a specific Gmail message by ID.
    """
    clean_message_id = str(message_id or "").strip()
    if not clean_message_id:
        raise ValueError("message_id is required")
    service = _build_gmail_service(project_logger, user_id=user_id, credential_store=credential_store)
    details = service.users().messages().get(
        userId="me",
        id=clean_message_id,
        format="full" if include_body else "metadata",
        metadataHeaders=["From", "To", "Subject", "Date"],
    ).execute()
    return _normalize_message_payload(details, include_body=include_body)


def search_email_attachments(
    project_logger,
    query="",
    filename_contains="",
    max_results=20,
    user_id=None,
    credential_store=None,
):
    """
    Search attachment metadata across Gmail messages.
    """
    clean_query = str(query or "").strip()
    clean_filename = str(filename_contains or "").strip().lower()
    limit = max(1, min(int(max_results or 20), 50))
    attachment_query = "has:attachment"
    if clean_query:
        attachment_query = f"{attachment_query} {clean_query}"

    search_result = search_emails(
        project_logger=project_logger,
        query=attachment_query,
        max_results=limit,
        include_body=False,
        user_id=user_id,
        credential_store=credential_store,
    )
    service = _build_gmail_service(project_logger, user_id=user_id, credential_store=credential_store)
    matches = []
    for email_item in search_result.get("emails", []):
        details = service.users().messages().get(
            userId="me",
            id=email_item.get("id"),
            format="full",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        attachments = _collect_attachments(details.get("payload", {}))
        for attachment in attachments:
            filename = str(attachment.get("filename", ""))
            if clean_filename and clean_filename not in filename.lower():
                continue
            matches.append(
                {
                    "message_id": email_item.get("id"),
                    "thread_id": email_item.get("thread_id"),
                    "subject": email_item.get("subject", ""),
                    "from": email_item.get("from", ""),
                    "filename": filename,
                    "mime_type": attachment.get("mime_type"),
                    "size": attachment.get("size", 0),
                    "attachment_id": attachment.get("attachment_id"),
                }
            )

    return {
        "query": clean_query,
        "filename_filter": clean_filename,
        "returned": len(matches),
        "attachments": matches,
    }


def analyze_email_attachment(
    project_logger,
    message_id,
    attachment_id=None,
    filename=None,
    max_chars=8000,
    user_id=None,
    credential_store=None,
):
    """
    Download and extract text content from one email attachment.
    """
    clean_message_id = str(message_id or "").strip()
    clean_attachment_id = str(attachment_id or "").strip()
    clean_filename = str(filename or "").strip()
    if not clean_message_id:
        raise ValueError("message_id is required")
    if not clean_attachment_id and not clean_filename:
        raise ValueError("attachment_id or filename is required")

    service = _build_gmail_service(project_logger, user_id=user_id, credential_store=credential_store)
    message = service.users().messages().get(
        userId="me",
        id=clean_message_id,
        format="full",
        metadataHeaders=["From", "To", "Subject", "Date"],
    ).execute()
    payload = message.get("payload", {}) or {}
    attachments = _collect_attachments(payload)
    selected_attachment = _select_attachment(
        attachments,
        attachment_id=clean_attachment_id,
        filename=clean_filename,
    )
    if selected_attachment is None:
        raise ValueError("Attachment not found for the provided selectors")

    inline_data = str(selected_attachment.get("inline_data", "")).strip()
    if inline_data:
        attachment_data = _decode_base64_bytes(inline_data)
    else:
        resolved_attachment_id = str(selected_attachment.get("attachment_id", "")).strip()
        if not resolved_attachment_id:
            raise ValueError("Attachment payload is missing attachment_id")
        attachment_response = service.users().messages().attachments().get(
            userId="me",
            messageId=clean_message_id,
            id=resolved_attachment_id,
        ).execute()
        attachment_data = _decode_base64_bytes(attachment_response.get("data", ""))
    extracted_text = _extract_attachment_text(
        content_bytes=attachment_data,
        filename=selected_attachment.get("filename", ""),
        mime_type=selected_attachment.get("mime_type", ""),
    )
    limit = max(200, min(int(max_chars or 8000), 20000))
    return {
        "message_id": clean_message_id,
        "attachment_id": selected_attachment.get("attachment_id"),
        "filename": selected_attachment.get("filename", ""),
        "mime_type": selected_attachment.get("mime_type", ""),
        "size": selected_attachment.get("size", 0),
        "content_preview": extracted_text[:limit],
        "content_length": len(extracted_text),
        "truncated": len(extracted_text) > limit,
    }


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


def _build_gmail_service(project_logger, user_id=None, credential_store=None):
    return gmail_connect(project_logger, user_id=user_id, credential_store=credential_store)


def _normalize_message_payload(message, include_body=False):
    payload = message.get("payload", {}) or {}
    headers = _extract_headers(payload)
    normalized = {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "snippet": message.get("snippet", ""),
        "internal_date": message.get("internalDate"),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "attachments": _collect_attachments(payload),
    }
    if include_body:
        normalized["body"] = _extract_message_body(payload)
    return normalized


def _extract_headers(payload):
    header_items = payload.get("headers", []) or []
    result = {}
    for item in header_items:
        name = str(item.get("name", "")).strip().lower()
        if not name:
            continue
        result[name] = str(item.get("value", "")).strip()
    return result


def _extract_message_body(payload):
    parts = payload.get("parts", []) or []
    body_data = payload.get("body", {}).get("data")
    if body_data:
        return _decode_base64_payload(body_data)

    plain_candidate = ""
    html_candidate = ""
    for part in parts:
        mime_type = str(part.get("mimeType", "")).lower()
        part_body = part.get("body", {}).get("data")
        nested_parts = part.get("parts", []) or []
        if mime_type == "text/plain" and part_body:
            plain_candidate = _decode_base64_payload(part_body)
            break
        if mime_type == "text/html" and part_body and not html_candidate:
            html_candidate = _decode_base64_payload(part_body)
        if nested_parts:
            nested_text = _extract_message_body(part)
            if nested_text:
                if mime_type == "text/plain":
                    return nested_text
                if not plain_candidate:
                    plain_candidate = nested_text
    return plain_candidate or html_candidate


def _collect_attachments(payload):
    attachments = []
    parts = payload.get("parts", []) or []
    for part in parts:
        filename = str(part.get("filename", "")).strip()
        body = part.get("body", {}) or {}
        attachment_id = body.get("attachmentId")
        inline_data = body.get("data")
        if filename and (attachment_id or inline_data):
            attachments.append(
                {
                    "filename": filename,
                    "mime_type": part.get("mimeType", ""),
                    "size": int(body.get("size", 0) or 0),
                    "attachment_id": attachment_id,
                    "inline_data": inline_data or "",
                }
            )
        nested_parts = part.get("parts", []) or []
        if nested_parts:
            attachments.extend(_collect_attachments(part))
    return attachments


def _decode_base64_payload(value):
    text = str(value or "").strip()
    if not text:
        return ""
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(f"{text}{padding}".encode("utf-8")).decode("utf-8", errors="replace")


def _decode_base64_bytes(value):
    text = str(value or "").strip()
    if not text:
        return b""
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(f"{text}{padding}".encode("utf-8"))


def _select_attachment(attachments, attachment_id="", filename=""):
    clean_filename = str(filename or "").strip().lower()
    for attachment in attachments:
        if attachment_id and attachment.get("attachment_id") == attachment_id:
            return attachment
        attachment_filename = attachment.get("filename", "").strip().lower()
        if clean_filename and attachment_filename == clean_filename:
            return attachment
        if clean_filename and clean_filename in attachment_filename:
            return attachment
    return None


def _extract_attachment_text(content_bytes, filename="", mime_type=""):
    clean_mime_type = str(mime_type or "").strip().lower().split(";", 1)[0].strip()
    extension = os.path.splitext(str(filename or "").lower())[1]

    if clean_mime_type.startswith("text/") or extension in (".txt", ".md", ".csv", ".tsv"):
        return content_bytes.decode("utf-8", errors="replace")
    if extension == ".pdf" or clean_mime_type == "application/pdf":
        return _extract_pdf_text(content_bytes)
    if extension == ".docx" or clean_mime_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        return _extract_docx_text(content_bytes)
    if extension in (".xlsx", ".xlsm") or clean_mime_type in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroenabled.12",
    ):
        return _extract_xlsx_text(content_bytes)
    raise ValueError(f"Unsupported attachment format: {mime_type or extension or 'unknown'}")


def _extract_pdf_text(content_bytes):
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise ValueError("PDF support requires pypdf installed") from error

    reader = PdfReader(io.BytesIO(content_bytes))
    chunks = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def _extract_docx_text(content_bytes):
    with zipfile.ZipFile(io.BytesIO(content_bytes)) as zip_file:
        document_xml = zip_file.read("word/document.xml")
    root = ET.fromstring(document_xml)
    text_nodes = root.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
    return "\n".join(node.text for node in text_nodes if node.text).strip()


def _extract_xlsx_text(content_bytes):
    with zipfile.ZipFile(io.BytesIO(content_bytes)) as zip_file:
        shared_strings = _xlsx_shared_strings(zip_file)
        sheet_names = [name for name in zip_file.namelist() if name.startswith("xl/worksheets/sheet")]
        rows = []
        for sheet_name in sheet_names:
            sheet_xml = zip_file.read(sheet_name)
            rows.extend(_xlsx_sheet_rows(sheet_xml, shared_strings))
    return "\n".join(row for row in rows if row).strip()


def _xlsx_shared_strings(zip_file):
    if "xl/sharedStrings.xml" not in zip_file.namelist():
        return []
    root = ET.fromstring(zip_file.read("xl/sharedStrings.xml"))
    strings = []
    for si in root.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
        texts = si.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
        strings.append("".join(node.text or "" for node in texts))
    return strings


def _xlsx_sheet_rows(sheet_xml, shared_strings):
    root = ET.fromstring(sheet_xml)
    cells = root.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c")
    values = []
    for cell in cells:
        cell_type = cell.attrib.get("t")
        value_node = cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
        if value_node is None or value_node.text is None:
            continue
        value_text = value_node.text
        if cell_type == "s":
            try:
                value_text = shared_strings[int(value_text)]
            except (ValueError, IndexError):
                pass
        values.append(str(value_text))
    return values
