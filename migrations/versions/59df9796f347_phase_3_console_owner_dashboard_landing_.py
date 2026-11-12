"""phase 3 console, owner dashboard, landing page

Revision ID: 59df9796f347
Revises: d39cc975a6c1
Create Date: 2026-11-11 19:19:18.692501

Hand-adjusted after autogenerate, for two reasons:

  1. `session_kind` is used by both `auth_sessions` and `audit_log`. Autogenerate
     emits an inline `sa.Enum` in each `create_table`, so the second one fails
     with "type session_kind already exists". The types are created once here,
     up front, and referenced with `create_type=False`.
  2. Autogenerate never writes `server_default`, so a plain SQL insert into
     these tables would have violated NOT NULL on the columns that the ORM
     fills in from a Python-side default.

`downgrade()` drops both types after the tables that use them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "59df9796f347"
down_revision: str | None = "d39cc975a6c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

session_kind = postgresql.ENUM(
    "admin", "owner", name="session_kind", create_type=False
)
signup_status = postgresql.ENUM(
    "new", "contacted", "converted", "rejected",
    name="signup_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    session_kind.create(bind, checkfirst=True)
    signup_status.create(bind, checkfirst=True)

    op.create_table(
        "admin_users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_admin_users_email"),
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", session_kind, nullable=False),
        sa.Column("admin_user_id", sa.UUID(), nullable=True),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.CheckConstraint(
            "(kind = 'admin' and admin_user_id is not null and client_id is null)"
            " or (kind = 'owner' and client_id is not null"
            " and admin_user_id is null)",
            name="ck_auth_session_subject",
        ),
        sa.ForeignKeyConstraint(
            ["admin_user_id"],
            ["admin_users.id"],
            name="fk_auth_sessions_admin_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_auth_sessions_client",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auth_session_expires", "auth_sessions", ["expires_at"], unique=False
    )
    op.create_index(
        op.f("ix_auth_sessions_token_hash"),
        "auth_sessions",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "login_codes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_login_codes_client",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_login_code_phone_at", "login_codes", ["phone", "created_at"], unique=False
    )

    op.create_table(
        "signups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_name", sa.String(length=200), nullable=False),
        sa.Column("contact_name", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'landing'"),
        ),
        sa.Column(
            "status", signup_status, nullable=False, server_default=sa.text("'new'")
        ),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_signups_client",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_signup_status_at", "signups", ["status", "created_at"], unique=False
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("actor_kind", session_kind, nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=200), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_audit_log_client",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_at", "audit_log", ["at"], unique=False)
    op.create_index("ix_audit_client_at", "audit_log", ["client_id", "at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_client_at", table_name="audit_log")
    op.drop_index("ix_audit_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_signup_status_at", table_name="signups")
    op.drop_table("signups")
    op.drop_index("ix_login_code_phone_at", table_name="login_codes")
    op.drop_table("login_codes")
    op.drop_index(op.f("ix_auth_sessions_token_hash"), table_name="auth_sessions")
    op.drop_index("ix_auth_session_expires", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("admin_users")

    bind = op.get_bind()
    signup_status.drop(bind, checkfirst=True)
    session_kind.drop(bind, checkfirst=True)
