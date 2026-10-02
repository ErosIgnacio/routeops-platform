"""Optional immutable route limits for imported vehicles.

Revision ID: e3a1b7c9d240
Revises: c951e2a7d430
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3a1b7c9d240"
down_revision: str | None = "c951e2a7d430"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "max_route_distance_meters",
    "max_driving_seconds",
    "max_delivery_tasks",
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("vehicles", sa.Column(column, sa.Integer(), nullable=True))
        op.create_check_constraint(
            f"ck_vehicles_{column}",
            "vehicles",
            f"{column} IS NULL OR {column} BETWEEN 1 AND 2147483647",
        )


def downgrade() -> None:
    # Historical published revisions are immutable. Do not silently discard their limits.
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM vehicles WHERE "
            + " OR ".join(f"{column} IS NOT NULL" for column in _COLUMNS)
            + ")"
        )
    ).scalar_one():
        raise RuntimeError("Cannot downgrade while published vehicles have route limits")
    for column in reversed(_COLUMNS):
        op.drop_constraint(f"ck_vehicles_{column}", "vehicles", type_="check")
        op.drop_column("vehicles", column)
