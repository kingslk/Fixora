"""Store one user-provided task screenshot as external artifact metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0003_task_input_image"
down_revision = "0002_task_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("image_path", sa.String(4096), nullable=True))
    op.add_column("tasks", sa.Column("image_name", sa.String(255), nullable=True))
    op.add_column("tasks", sa.Column("image_mime", sa.String(64), nullable=True))
    op.add_column("tasks", sa.Column("image_size", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "image_size")
    op.drop_column("tasks", "image_mime")
    op.drop_column("tasks", "image_name")
    op.drop_column("tasks", "image_path")
