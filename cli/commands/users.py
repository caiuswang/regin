"""`regin users ...` subcommands (accounts + auth table bootstrap)."""

from __future__ import annotations

from typing import Optional

import typer


users_app = typer.Typer(
    name="users", help="Manage user accounts",
    no_args_is_help=True,
)


_AUTH_TABLES = ("users", "audit_log")


@users_app.command("init-db", help="Create the users/audit_log tables on the shared-mode MySQL auth DB (standalone: checks `regin init` created them)")
def cmd_users_init_db() -> None:
    from lib.settings import settings
    if settings.mode == 'shared':
        from lib.mysql_db import init_mysql
        init_mysql()
        print("MySQL tables created (users, audit_log).")
        return
    # Standalone auth lives in the primary DB, whose shape belongs to
    # schema.sql + alembic; creating the tables here would build an unstamped
    # DB that diverges from a `regin init` one.
    import os

    from sqlalchemy import inspect

    from lib.orm import engine
    present = (set(inspect(engine.get_auth_engine()).get_table_names())
               if os.path.exists(engine.DB_PATH) else set())
    missing = [t for t in _AUTH_TABLES if t not in present]
    if missing:
        print(f"Missing auth tables: {', '.join(missing)}. "
              "Run `regin init` (new DB) or `regin migrate` (existing DB).")
        raise typer.Exit(1)
    print("SQLite auth tables present (users, audit_log).")


@users_app.command("list", help="List all users")
def cmd_users_list() -> None:
    from lib.auth import list_users
    users = list_users()
    if not users:
        print("No users registered.")
        return
    print(f"{'ID':>4}  {'USERNAME':20s}  {'DISPLAY NAME':20s}  {'ROLE':8s}  LAST LOGIN")
    for u in users:
        print(f"{u['id']:>4}  {u['username']:20s}  {u['display_name']:20s}  "
              f"{u['role']:8s}  {u['last_login'] or 'never'}")


@users_app.command("create", help="Create a new user")
def cmd_users_create(
    username: str = typer.Argument(..., help="Username"),
    password: str = typer.Argument(..., help="Password"),
    display_name: Optional[str] = typer.Option(
        None, "--display-name", help="Display name (defaults to username)",
    ),
    role: Optional[str] = typer.Option(
        None, "--role",
        help="Role (default: editor, or admin if first user). Choices: admin|editor|viewer",
    ),
) -> None:
    if role is not None and role not in ('admin', 'editor', 'viewer'):
        print(f"Error: invalid --role {role!r}; choose from admin, editor, viewer")
        raise typer.Exit(2)
    from lib.auth import register_user
    try:
        user = register_user(
            username, display_name or username, password, role=role,
        )
        print(f"Created user '{user['username']}' with role '{user['role']}'")
    except Exception as exc:
        print(f"Error: {exc}")
        raise typer.Exit(1)


@users_app.command("reset-password", help="Reset a user password")
def cmd_users_reset_password(
    username: str = typer.Argument(..., help="Username"),
    password: str = typer.Argument(..., help="New password"),
) -> None:
    from lib.auth import reset_password
    if reset_password(username, password):
        print(f"Password reset for '{username}'")
    else:
        print(f"User '{username}' not found.")
        raise typer.Exit(1)


@users_app.command("set-role", help="Change a user role")
def cmd_users_set_role(
    username: str = typer.Argument(..., help="Username"),
    role: str = typer.Argument(..., help="New role (admin|editor|viewer)"),
) -> None:
    if role not in ('admin', 'editor', 'viewer'):
        print(f"Error: invalid role {role!r}; choose from admin, editor, viewer")
        raise typer.Exit(2)
    from lib.auth import list_users, set_role
    users = {u['username']: u['id'] for u in list_users()}
    if username not in users:
        print(f"User '{username}' not found.")
        raise typer.Exit(1)
    if set_role(users[username], role):
        print(f"Role for '{username}' set to '{role}'")
    else:
        print(f"Invalid role: {role}")
        raise typer.Exit(1)


@users_app.command("delete", help="Delete a user")
def cmd_users_delete(
    username: str = typer.Argument(..., help="Username"),
) -> None:
    from lib.auth import list_users, delete_user
    users = {u['username']: u['id'] for u in list_users()}
    if username not in users:
        print(f"User '{username}' not found.")
        raise typer.Exit(1)
    delete_user(users[username])
    print(f"Deleted user '{username}'")
