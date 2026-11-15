"""signup niche country and consent

Revision ID: a4e91c73b0d2
Revises: f22cf4d5a57c
Create Date: 2026-11-15 09:40:00.000000

Six additive, nullable columns on `signups`. Nothing dropped, nothing renamed,
no existing row changes meaning - so a rollback is a `downgrade` and not a
restore.

Why these are columns rather than text appended to `signups.message`: four of
the six are read by the console when it converts a signup into a shop. The
niche picks the template, the country picks the timezone and the phone region,
and the languages seed `enabled_languages`. A human re-typing those out of a
free-text blob is how a clinic ends up live with the wrong boundary block.

`consent_at` is the odd one out, and it is the one that is not optional in
spirit: GDPR Art. 7(1) puts the burden of *demonstrating* consent on the
controller. A checkbox that is validated and then forgotten demonstrates
nothing. The row records the moment it was ticked.

Every column is nullable on purpose. Signups taken before this migration were
US trades who answered a different set of questions, and backfilling them with
`'ES'` or `'dental'` would make a guess indistinguishable from an answer.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4e91c73b0d2"
down_revision: str | None = "f22cf4d5a57c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("signups", sa.Column("niche", sa.String(length=32), nullable=True))
    op.add_column("signups", sa.Column("country", sa.String(length=2), nullable=True))
    op.add_column(
        "signups",
        sa.Column("languages", postgresql.ARRAY(sa.String()), nullable=True),
    )
    op.add_column("signups", sa.Column("crm_tool", sa.String(length=64), nullable=True))
    op.add_column(
        "signups", sa.Column("website_url", sa.String(length=300), nullable=True)
    )
    op.add_column(
        "signups",
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("signups", "consent_at")
    op.drop_column("signups", "website_url")
    op.drop_column("signups", "crm_tool")
    op.drop_column("signups", "languages")
    op.drop_column("signups", "country")
    op.drop_column("signups", "niche")
