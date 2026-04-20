from __future__ import annotations

import os

from assistant_connector.file_store import ACCEPTED_EXTENSIONS, ACCEPTED_EXTENSIONS_DISPLAY, FileStore
from assistant_connector.models import ToolExecutionContext


def _get_file_store(context: ToolExecutionContext) -> FileStore:
    if context.file_store is None:
        raise RuntimeError("FileStore not configured in this runtime context.")
    return context.file_store


def list_user_files(arguments: dict, context: ToolExecutionContext) -> dict:
    """List all files uploaded by the user."""
    store = _get_file_store(context)
    files = store.list_files(user_id=context.user_id)
    return {
        "count": len(files),
        "files": [
            {
                "file_id": f["file_id"],
                "name": f["original_name"],
                "size_bytes": f["file_size"],
                "context": f["context_description"],
                "uploaded_at": f["uploaded_at"],
            }
            for f in files
        ],
    }


def read_file_content(arguments: dict, context: ToolExecutionContext) -> dict:
    """Extract and return text content from a stored user file."""
    file_id = str(arguments.get("file_id", "")).strip()
    if not file_id:
        return {"error": "missing_file_id", "message": "Informe o file_id do arquivo."}

    store = _get_file_store(context)
    record = store.get_file(user_id=context.user_id, file_id=file_id)
    if record is None:
        return {
            "error": "file_not_found",
            "message": f"Arquivo com id '{file_id}' não encontrado para este usuário.",
        }

    file_path = store.resolve_file_path(user_id=context.user_id, file_id=file_id)
    if file_path is None:
        return {
            "error": "file_missing_on_disk",
            "message": "O registro existe mas o arquivo não foi encontrado no disco.",
        }

    ext = os.path.splitext(record["original_name"])[1].lower()
    try:
        content = _extract_text(file_path, ext)
    except Exception as exc:
        return {
            "error": "extraction_failed",
            "message": f"Não foi possível extrair o conteúdo: {exc}",
        }

    max_chars = int(arguments.get("max_chars", 8000))
    truncated = len(content) > max_chars
    return {
        "file_id": file_id,
        "name": record["original_name"],
        "context": record["context_description"],
        "content": content[:max_chars],
        "chars_returned": min(len(content), max_chars),
        "truncated": truncated,
    }


def delete_user_file(arguments: dict, context: ToolExecutionContext) -> dict:
    """Delete a stored user file."""
    file_id = str(arguments.get("file_id", "")).strip()
    if not file_id:
        return {"error": "missing_file_id", "message": "Informe o file_id do arquivo."}

    store = _get_file_store(context)
    record = store.get_file(user_id=context.user_id, file_id=file_id)
    if record is None:
        return {
            "error": "file_not_found",
            "message": f"Arquivo com id '{file_id}' não encontrado para este usuário.",
        }

    deleted = store.delete_file(user_id=context.user_id, file_id=file_id)
    if deleted:
        return {"status": "deleted", "file_id": file_id, "name": record["original_name"]}
    return {"error": "delete_failed", "message": "Não foi possível deletar o arquivo."}


def _extract_text(file_path: str, ext: str) -> str:
    if ext in {".txt", ".csv", ".md"}:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    if ext == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            raise RuntimeError("pypdf não está instalado.")
        reader = PdfReader(file_path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip()

    if ext == ".docx":
        try:
            import docx  # type: ignore
        except ImportError:
            raise RuntimeError("python-docx não está instalado.")
        doc = docx.Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)

    raise ValueError(
        f"Formato '{ext}' não suportado para leitura. "
        f"Formatos aceitos: {ACCEPTED_EXTENSIONS_DISPLAY}"
    )
