"""CLI utility to manage web users.

Usage:
    python -m web_app.manage_users create  --username NAME --password PASS [--display-name NAME]
    python -m web_app.manage_users list
    python -m web_app.manage_users deactivate --username NAME
    python -m web_app.manage_users change-password --username NAME --password NEWPASS
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv


def _get_db_path() -> str:
    default = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "assistant_memory.sqlite3")
    )
    return os.getenv("ASSISTANT_MEMORY_PATH", default)


def _create(args: argparse.Namespace) -> None:
    from web_app.user_store import WebUserStore

    store = WebUserStore(_get_db_path())
    try:
        user = store.create_user(
            username=args.username,
            password=args.password,
            display_name=args.display_name or args.username,
        )
        print(f"✅ User created: {user['username']} (id: {user['id']})")
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)


def _list_users(_args: argparse.Namespace) -> None:
    from web_app.user_store import WebUserStore

    store = WebUserStore(_get_db_path())
    users = store.list_users()
    if not users:
        print("No users found.")
        return
    for u in users:
        status = "active" if u["is_active"] else "inactive"
        print(f"  {u['username']:<20} {u['display_name']:<20} [{status}]  (id: {u['id']})")


def _deactivate(args: argparse.Namespace) -> None:
    from web_app.user_store import WebUserStore

    store = WebUserStore(_get_db_path())
    if store.deactivate_user(args.username):
        print(f"✅ User '{args.username}' deactivated.")
    else:
        print(f"❌ User '{args.username}' not found.", file=sys.stderr)
        sys.exit(1)


def _change_password(args: argparse.Namespace) -> None:
    from web_app.user_store import WebUserStore

    store = WebUserStore(_get_db_path())
    try:
        if store.change_password(args.username, args.password):
            print(f"✅ Password changed for '{args.username}'.")
        else:
            print(f"❌ User '{args.username}' not found.", file=sys.stderr)
            sys.exit(1)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Manage web users for the personal assistant PWA.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create a new user")
    create_parser.add_argument("--username", required=True)
    create_parser.add_argument("--password", required=True)
    create_parser.add_argument("--display-name", default="")
    create_parser.set_defaults(func=_create)

    list_parser = subparsers.add_parser("list", help="List all users")
    list_parser.set_defaults(func=_list_users)

    deactivate_parser = subparsers.add_parser("deactivate", help="Deactivate a user")
    deactivate_parser.add_argument("--username", required=True)
    deactivate_parser.set_defaults(func=_deactivate)

    change_pw_parser = subparsers.add_parser("change-password", help="Change user password")
    change_pw_parser.add_argument("--username", required=True)
    change_pw_parser.add_argument("--password", required=True)
    change_pw_parser.set_defaults(func=_change_password)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
