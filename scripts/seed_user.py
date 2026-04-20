"""Seed an initial web user if SEED_USERNAME and SEED_PASSWORD are set.

Idempotent: skips creation if the username already exists.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    username = os.getenv("SEED_USERNAME", "").strip()
    password = os.getenv("SEED_PASSWORD", "").strip()

    if not username or not password:
        return

    db_path = os.getenv(
        "ASSISTANT_MEMORY_PATH",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assistant_memory.sqlite3")),
    )
    display_name = os.getenv("SEED_DISPLAY_NAME", username).strip()

    # Import here so dotenv is loaded first
    from web_app.user_store import WebUserStore

    store = WebUserStore(db_path)

    if store.get_user_by_username(username):
        print(f"[seed] User '{username}' already exists, skipping.")
        return

    try:
        user = store.create_user(username=username, password=password, display_name=display_name)
        print(f"[seed] User created: {user['username']} (id: {user['id']})")
    except ValueError as exc:
        print(f"[seed] Failed to create user: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
