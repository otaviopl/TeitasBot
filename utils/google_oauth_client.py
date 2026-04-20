from __future__ import annotations

import os


def load_google_client_config_from_env(*, redirect_uri: str | None = None) -> dict | None:
    client_id = str(os.getenv("GOOGLE_CLIENT_ID", "")).strip()
    client_secret = str(os.getenv("GOOGLE_CLIENT_SECRET", "")).strip()
    if not client_id or not client_secret:
        return None

    project_id = str(os.getenv("GOOGLE_PROJECT_ID", "")).strip()
    auth_uri = str(os.getenv("GOOGLE_AUTH_URI", "https://accounts.google.com/o/oauth2/auth")).strip()
    token_uri = str(os.getenv("GOOGLE_TOKEN_URI", "https://oauth2.googleapis.com/token")).strip()
    auth_provider_x509_cert_url = str(
        os.getenv("GOOGLE_AUTH_PROVIDER_CERT_URL", "https://www.googleapis.com/oauth2/v1/certs")
    ).strip()
    client_config = {
        "web": {
            "client_id": client_id,
            "project_id": project_id or None,
            "auth_uri": auth_uri,
            "token_uri": token_uri,
            "auth_provider_x509_cert_url": auth_provider_x509_cert_url,
            "client_secret": client_secret,
            "redirect_uris": [redirect_uri] if redirect_uri else [],
        }
    }
    return client_config
