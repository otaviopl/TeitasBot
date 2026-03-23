from __future__ import annotations

import os
import re

from gmail_connector import gmail_connector

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def send_email(arguments, context):
    subject = str(arguments.get("subject", "")).strip()
    body = str(arguments.get("body", "")).strip()
    recipient_input = str(arguments.get("recipient_email", "")).strip()
    recipient, recipient_source = (
        _resolve_recipient_email(recipient_input, context)
        if recipient_input
        else (_get_default_recipient(context), "contacts_csv")
    )
    reply_to_message_id = str(arguments.get("reply_to_message_id", "")).strip()
    if not recipient:
        raise ValueError("recipient_email is required")
    if not subject:
        raise ValueError("subject is required")
    if not body:
        raise ValueError("body is required")

    tone = str(arguments.get("tone_override", "")).strip() or _get_email_tone(context)
    signature = _get_email_signature(context)

    final_subject = _apply_subject_prefix(subject, context)
    final_body = _compose_email_body(body, signature=signature)

    send_result = gmail_connector.send_custom_email(
        project_logger=context.project_logger,
        subject=final_subject,
        body_text=final_body,
        email_to=recipient,
        body_subtype="plain",
        reply_to_message_id=reply_to_message_id or None,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    result = {
        "status": "sent",
        "subject": final_subject,
        "recipient_email": recipient,
        "tone": tone,
        "signature_applied": bool(signature),
        "provider_result": send_result,
    }
    if recipient_source == "explicit_email":
        result["suggest_save_contact"] = True
    return result


def search_emails(arguments, context):
    query = str(arguments.get("query", "")).strip()
    max_results = _clamp_int(arguments.get("max_results", 10), minimum=1, maximum=50, default=10)
    include_body = bool(arguments.get("include_body", False))
    return gmail_connector.search_emails(
        project_logger=context.project_logger,
        query=query,
        max_results=max_results,
        include_body=include_body,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )


def read_email(arguments, context):
    message_id = str(arguments.get("message_id", "")).strip()
    if not message_id:
        raise ValueError("message_id is required")
    include_body = bool(arguments.get("include_body", True))
    return gmail_connector.read_email(
        project_logger=context.project_logger,
        message_id=message_id,
        include_body=include_body,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )


def search_email_attachments(arguments, context):
    query = str(arguments.get("query", "")).strip()
    filename_contains = str(arguments.get("filename_contains", "")).strip()
    max_results = _clamp_int(arguments.get("max_results", 20), minimum=1, maximum=50, default=20)
    return gmail_connector.search_email_attachments(
        project_logger=context.project_logger,
        query=query,
        filename_contains=filename_contains,
        max_results=max_results,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )


def analyze_email_attachment(arguments, context):
    message_id = str(arguments.get("message_id", "")).strip()
    attachment_id = str(arguments.get("attachment_id", "")).strip()
    filename = str(arguments.get("filename", "")).strip()
    if not message_id:
        raise ValueError("message_id is required")
    if not attachment_id and not filename:
        raise ValueError("attachment_id or filename is required")
    max_chars = _clamp_int(arguments.get("max_chars", 8000), minimum=200, maximum=20000, default=8000)
    return gmail_connector.analyze_email_attachment(
        project_logger=context.project_logger,
        message_id=message_id,
        attachment_id=attachment_id or None,
        filename=filename or None,
        max_chars=max_chars,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )


def _apply_subject_prefix(subject, context=None):
    store = getattr(context, "user_credential_store", None)
    user_id = getattr(context, "user_id", None)
    if store is not None and user_id is not None:
        prefix = store.get_credential(user_id, "email_subject_prefix") or ""
    else:
        prefix = str(os.getenv("EMAIL_ASSISTANT_SUBJECT_PREFIX", "")).strip()
    if not prefix:
        return subject
    return f"{prefix} {subject}".strip()


def _compose_email_body(body, *, signature):
    sections = [body]
    if signature and not _body_already_has_signature(body, signature):
        sections.append(f"\n\n{signature}")
    return "".join(sections).strip()


def _get_email_tone(context=None):
    store = getattr(context, "user_credential_store", None)
    user_id = getattr(context, "user_id", None)
    if store is not None and user_id is not None:
        value = store.get_credential(user_id, "email_tone")
        if value:
            return value
    return str(
        os.getenv("EMAIL_ASSISTANT_TONE", "claro, cordial e objetivo")
    ).strip() or "claro, cordial e objetivo"


def _get_email_signature(context=None):
    store = getattr(context, "user_credential_store", None)
    user_id = getattr(context, "user_id", None)
    if store is not None and user_id is not None:
        return store.get_credential(user_id, "email_signature") or ""
    return str(os.getenv("EMAIL_ASSISTANT_SIGNATURE", "")).strip()


def _get_default_recipient(context=None):
    store = getattr(context, "user_credential_store", None)
    user_id = getattr(context, "user_id", None)
    if store is not None and user_id is not None:
        recipient = store.get_credential(user_id, "email_to")
        if recipient:
            return str(recipient).strip()
    env_recipient = str(os.getenv("EMAIL_TO", "")).strip()
    if env_recipient:
        return env_recipient
    return _resolve_default_contact_recipient(context)


def _clamp_int(value, *, minimum, maximum, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _body_already_has_signature(body: str, signature: str) -> bool:
    body_text = str(body or "").strip()
    signature_text = str(signature or "").strip()
    if not body_text or not signature_text:
        return False
    if signature_text in body_text:
        return True

    def _normalize(text: str) -> str:
        return " ".join(text.split()).casefold()

    normalized_signature = _normalize(signature_text)
    normalized_tail = _normalize(body_text[-1200:])
    return bool(normalized_signature and normalized_signature in normalized_tail)


def _resolve_recipient_email(recipient_input: str, context=None) -> tuple[str, str]:
    from assistant_connector.tools import contacts_tools

    clean_recipient = str(recipient_input or "").strip()
    if not clean_recipient:
        return "", "explicit_email"
    if _looks_like_email(clean_recipient):
        return clean_recipient, "explicit_email"
    resolved = contacts_tools.resolve_contact_email(clean_recipient, context)
    return str(resolved["email"]).strip(), str(resolved.get("source", "contacts_csv"))


def _resolve_default_contact_recipient(context=None) -> str:
    from assistant_connector.tools import contacts_tools

    memories_dir = str(getattr(context, "memories_dir", "") or "").strip()
    if not memories_dir:
        return ""
    try:
        resolved = contacts_tools.resolve_contact_email(
            "meu email pessoal",
            context,
            raise_on_ambiguous=False,
        )
    except (FileNotFoundError, ValueError):
        return ""
    if not resolved:
        return ""
    return str(resolved.get("email", "")).strip()


def _looks_like_email(value: str) -> bool:
    return bool(_EMAIL_PATTERN.fullmatch(str(value or "").strip()))
