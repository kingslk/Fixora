"""Store user feedback for each TaskAttempt."""

import sqlalchemy as sa
from alembic import op

revision = "0004_task_feedback"
down_revision = "0003_task_input_image"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_attempts", sa.Column("feedback_rating", sa.String(32), nullable=True))
    op.add_column("task_attempts", sa.Column("feedback_reason", sa.Text(), nullable=True))
    op.add_column("task_attempts", sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("task_attempts", "feedback_at")
    op.drop_column("task_attempts", "feedback_reason")
    op.drop_column("task_attempts", "feedback_rating")
