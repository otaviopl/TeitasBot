from __future__ import annotations

from assistant_connector.models import ToolExecutionContext
from assistant_connector.user_credential_store import ALL_VALID_KEYS

_KEY_DESCRIPTIONS = {
    "notion_api_key": "Chave de integração do Notion (começa com secret_...)",
    "notion_notes_db_id": "ID do banco de notas no Notion",
    "email_from": "Endereço de email do remetente (Gmail)",
    "email_to": "Endereço de email para notificações",
    "display_name": "Seu nome para exibição nos emails",
    "email_tone": "Tom dos emails (ex: formal, casual)",
    "email_signature": "Assinatura dos emails",
    "email_style_guide": "Guia de estilo para redigir emails",
    "email_subject_prefix": "Prefixo no assunto dos emails",
    "google_token_json": "Token OAuth do Google (Gmail + Calendar) — gerado via /google_auth",
}


def manage_user_credentials(arguments: dict, context: ToolExecutionContext) -> dict:
    """Manage per-user credentials stored securely in SQLite."""
    store = context.user_credential_store
    if store is None:
        return {
            "error": "credential_store_unavailable",
            "message": "Sistema de credenciais não disponível.",
        }

    action = str(arguments.get("action", "")).strip().lower()
    user_id = str(context.user_id)

    if action == "set":
        key = str(arguments.get("key", "")).strip().lower()
        value = str(arguments.get("value", "")).strip()
        if not key:
            return {"error": "missing_key", "message": "Parâmetro 'key' é obrigatório."}
        if key not in ALL_VALID_KEYS:
            return {
                "error": "invalid_key",
                "message": f"Chave inválida: '{key}'. Chaves válidas: {sorted(ALL_VALID_KEYS)}",
            }
        if not value:
            return {"error": "missing_value", "message": "Parâmetro 'value' não pode ser vazio."}
        store.set_credential(user_id, key, value)
        if context.project_logger:
            context.project_logger.info("User %s configured credential: %s", user_id, key)
        integrations = store.check_integrations(user_id)
        status_lines = [f"{'✅' if ok else '❌'} {name}" for name, ok in integrations.items()]
        return {
            "success": True,
            "key": key,
            "message": f"Credencial '{key}' salva com sucesso.",
            "integrations_status": integrations,
            "summary": "\n".join(status_lines),
        }

    elif action == "list_configured":
        keys = store.list_configured_keys(user_id)
        if not keys:
            return {"configured_keys": [], "message": "Nenhuma credencial configurada ainda."}
        return {
            "configured_keys": keys,
            "descriptions": {k: _KEY_DESCRIPTIONS.get(k, k) for k in keys},
        }

    elif action == "delete":
        key = str(arguments.get("key", "")).strip().lower()
        if not key:
            return {"error": "missing_key", "message": "Parâmetro 'key' é obrigatório."}
        deleted = store.delete_credential(user_id, key)
        if deleted:
            if context.project_logger:
                context.project_logger.info("User %s deleted credential: %s", user_id, key)
            return {"success": True, "message": f"Credencial '{key}' removida."}
        return {"success": False, "message": f"Credencial '{key}' não encontrada."}

    elif action == "check_integrations":
        integrations = store.check_integrations(user_id)
        configured_count = len(store.list_configured_keys(user_id))
        lines = [f"{'✅' if ok else '❌'} {name}" for name, ok in integrations.items()]
        return {
            "integrations": integrations,
            "configured_keys_count": configured_count,
            "summary": "\n".join(lines),
        }

    return {
        "error": "unknown_action",
        "message": f"Ação desconhecida: '{action}'. Use: set, list_configured, delete, check_integrations",
    }
