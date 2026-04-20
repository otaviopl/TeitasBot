from __future__ import annotations

import csv
import os
import re
import unicodedata


CONTACTS_CSV_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "memories", "contacts.csv")
)
REQUIRED_COLUMNS = ("Nome", "email", "telefone", "relacionamento")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _resolve_contacts_path(context) -> str:
    """Return per-user contacts.csv path if it exists, otherwise fall back to the global default."""
    memories_dir = str(getattr(context, "memories_dir", "") or "").strip()
    if memories_dir:
        user_contacts = os.path.join(memories_dir, "contacts.csv")
        if os.path.isfile(user_contacts):
            return user_contacts
    return CONTACTS_CSV_PATH


def _resolve_contacts_write_path(context) -> str:
    memories_dir = str(getattr(context, "memories_dir", "") or "").strip()
    if memories_dir:
        os.makedirs(memories_dir, exist_ok=True, mode=0o700)
        return os.path.join(memories_dir, "contacts.csv")

    default_dir = os.path.dirname(CONTACTS_CSV_PATH)
    os.makedirs(default_dir, exist_ok=True)
    return CONTACTS_CSV_PATH


def search_contacts(arguments, context):
    query = str(arguments.get("query", "")).strip().lower()
    try:
        limit = int(arguments.get("limit", 20))
    except (ValueError, TypeError):
        raise ValueError("limit must be a valid integer")
    limit = min(max(limit, 1), 100)

    contacts_path = _resolve_contacts_path(context)
    contacts = _read_contacts_csv(contacts_path)
    if query:
        scored_contacts = []
        for contact in contacts:
            score = _score_contact_for_query(contact, query)
            if score > 0:
                scored_contacts.append((score, contact))
        scored_contacts.sort(
            key=lambda item: (
                item[0],
                item[1].get("Nome", "").lower(),
                item[1].get("email", "").lower(),
            ),
            reverse=True,
        )
        contacts = [contact for _, contact in scored_contacts]

    return {
        "total": len(contacts),
        "returned": min(limit, len(contacts)),
        "contacts": contacts[:limit],
    }


def _read_contacts_csv(contacts_path: str = CONTACTS_CSV_PATH):
    if not os.path.exists(contacts_path):
        raise FileNotFoundError(f"Contacts file not found: {contacts_path}")

    with open(contacts_path, "r", encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file, delimiter=",")
        header = [str(column).strip() for column in (reader.fieldnames or []) if column is not None]
        missing_columns = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing_columns:
            raise ValueError(
                "Contacts CSV is missing required columns: " + ", ".join(missing_columns)
            )

        contacts = []
        for row in reader:
            clean_row = {str(key).strip(): value for key, value in row.items() if key is not None}
            contacts.append(
                {
                    "Nome": str(clean_row.get("Nome", "")).strip(),
                    "email": str(clean_row.get("email", "")).strip(),
                    "telefone": str(clean_row.get("telefone", "")).strip(),
                    "relacionamento": str(clean_row.get("relacionamento", "")).strip(),
                }
            )
        return contacts


def resolve_contact_email(query, context, *, raise_on_ambiguous=True):
    clean_query = str(query or "").strip()
    if not clean_query:
        raise ValueError("recipient query is required")

    if _looks_like_email(clean_query):
        return {
            "email": clean_query,
            "source": "explicit_email",
            "contact": None,
        }

    contacts_path = _resolve_contacts_path(context)
    contacts = _read_contacts_csv(contacts_path)
    scored_contacts = []
    for contact in contacts:
        email = str(contact.get("email", "")).strip()
        if not _looks_like_email(email):
            continue
        score = _score_contact_for_query(contact, clean_query)
        if score > 0:
            scored_contacts.append((score, contact))
    if not scored_contacts:
        raise ValueError(
            "Could not resolve recipient from contacts.csv. "
            "Use a full email address or a more specific contact query."
        )

    scored_contacts.sort(
        key=lambda item: (
            item[0],
            item[1].get("Nome", "").lower(),
            item[1].get("email", "").lower(),
        ),
        reverse=True,
    )
    top_score = scored_contacts[0][0]
    top_contacts = [contact for score, contact in scored_contacts if score == top_score]
    top_emails = sorted({str(contact.get("email", "")).strip() for contact in top_contacts if contact.get("email")})
    if len(top_emails) == 1:
        selected = next(contact for contact in top_contacts if str(contact.get("email", "")).strip() == top_emails[0])
        return {
            "email": top_emails[0],
            "source": "contacts_csv",
            "contact": selected,
        }

    if raise_on_ambiguous:
        options = "; ".join(
            f"{str(contact.get('Nome', '')).strip()} <{str(contact.get('email', '')).strip()}>"
            for contact in top_contacts[:5]
        )
        raise ValueError(
            "recipient query is ambiguous. Matches: "
            f"{options}. Please specify the contact name or exact email."
        )
    return None


def register_contact_memory(arguments, context):
    name = str(arguments.get("name", "")).strip()
    email = str(arguments.get("email", "")).strip()
    phone = str(arguments.get("phone", "")).strip()
    relationship = str(arguments.get("relationship", "")).strip()

    if not name:
        raise ValueError("name is required")
    if not email and not phone:
        raise ValueError("email or phone is required")

    csv_path = _resolve_contacts_write_path(context)
    contact_row = {
        "Nome": name,
        "email": email,
        "telefone": phone,
        "relacionamento": relationship,
    }
    _append_contact_csv(csv_path, contact_row)

    return {
        "status": "ok",
        "contact": contact_row,
        "contacts_csv_path": csv_path,
    }


def _append_contact_csv(csv_path: str, contact_row: dict[str, str]) -> None:
    file_exists = os.path.isfile(csv_path)
    needs_header = (not file_exists) or os.path.getsize(csv_path) == 0
    with open(csv_path, "a", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=REQUIRED_COLUMNS, delimiter=",")
        if needs_header:
            writer.writeheader()
        writer.writerow(contact_row)


def _score_contact_for_query(contact: dict[str, str], query: str) -> int:
    normalized_query = _normalize_text(query)
    query_tokens = set(_tokenize(normalized_query))
    if not query_tokens:
        return 0

    name = _normalize_text(contact.get("Nome", ""))
    email = _normalize_text(contact.get("email", ""))
    phone = _normalize_text(contact.get("telefone", ""))
    relationship = _normalize_text(contact.get("relacionamento", ""))
    searchable = f"{name} {email} {phone} {relationship}".strip()

    score = 0
    if normalized_query == email:
        score += 500
    if normalized_query and normalized_query in searchable:
        score += 80
    if normalized_query and normalized_query in name:
        score += 120
    if normalized_query and normalized_query in relationship:
        score += 130
    if normalized_query and normalized_query in email:
        score += 160

    for token in query_tokens:
        if token in name:
            score += 30
        if token in relationship:
            score += 40
        if token in email:
            score += 50
        if token in phone:
            score += 10

    personal_tokens = {"pessoal", "personal", "meu", "minha", "proprio", "propria", "proprio", "eu"}
    professional_tokens = {"profissional", "trabalho", "work", "empresa", "corporativo"}
    query_has_personal = any(token in query_tokens for token in personal_tokens)
    query_has_professional = any(token in query_tokens for token in professional_tokens)
    relationship_tokens = set(_tokenize(relationship))
    if query_has_personal and relationship_tokens.intersection({"pessoal", "personal", "esposa", "familia", "minha", "meu"}):
        score += 120
    if query_has_professional and relationship_tokens.intersection({"profissional", "trabalho", "empresa", "socio", "cto", "ceo"}):
        score += 120

    return score


def _normalize_text(value: str) -> str:
    raw = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _tokenize(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or ""))


def _looks_like_email(value: str) -> bool:
    return bool(_EMAIL_PATTERN.fullmatch(str(value or "").strip()))
