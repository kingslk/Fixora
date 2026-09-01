"""Expand-first TaskAttempt schema. Keep old Task columns; do not drop history on downgrade."""

import sqlalchemy as sa
from alembic import op

revision = "0002_task_attempts"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

ACTIVE = (
    "queued",
    "capturing_source",
    "syncing_repository",
    "analyzing",
    "validating",
    "committing",
    "stale",
)


def _drop_named_or_unnamed(inspector, table: str, columns: set[str], *, unique: bool) -> None:
    if unique:
        for item in inspector.get_unique_constraints(table):
            if set(item.get("column_names") or []) == columns:
                op.drop_constraint(item["name"], table, type_="unique")
                return
    for item in inspector.get_indexes(table):
        if item.get("unique") and set(item.get("column_names") or []) == columns:
            op.drop_index(item["name"], table_name=table)
            return


def upgrade() -> None:
    op.create_table(
        "task_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(48), nullable=False),
        sa.Column("base_sha", sa.String(64)),
        sa.Column("forced_reason", sa.Text()),
        sa.Column("branch_name", sa.String(255)),
        sa.Column("commit_sha", sa.String(64)),
        sa.Column("error", sa.Text()),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("execution_started_at", sa.DateTime(timezone=True)),
        sa.Column("execution_finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "attempt_no", name="uq_task_attempts_task_no"),
    )
    op.create_index("ix_task_attempts_task_id", "task_attempts", ["task_id"])
    op.create_index("ix_task_attempts_status", "task_attempts", ["status"])

    op.add_column("tasks", sa.Column("current_attempt_id", sa.Integer(), nullable=True))
    op.create_index("ix_tasks_current_attempt_id", "tasks", ["current_attempt_id"])
    op.create_foreign_key(
        "fk_tasks_current_attempt_id",
        "tasks",
        "task_attempts",
        ["current_attempt_id"],
        ["id"],
    )

    for table in ("task_steps", "task_events", "source_captures", "change_sets", "test_runs"):
        op.add_column(table, sa.Column("task_attempt_id", sa.Integer(), nullable=True))
        op.create_index(f"ix_{table}_task_attempt_id", table, ["task_attempt_id"])
        op.create_foreign_key(
            f"fk_{table}_task_attempt_id",
            table,
            "task_attempts",
            ["task_attempt_id"],
            ["id"],
        )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _drop_named_or_unnamed(inspector, "task_steps", {"task_id", "position"}, unique=True)
    _drop_named_or_unnamed(inspector, "task_events", {"task_id", "seq"}, unique=True)
    _drop_named_or_unnamed(inspector, "source_captures", {"task_id"}, unique=True)

    op.create_unique_constraint(
        "uq_task_steps_attempt_pos", "task_steps", ["task_attempt_id", "position"]
    )
    op.create_unique_constraint(
        "uq_task_events_attempt_seq", "task_events", ["task_attempt_id", "seq"]
    )
    op.create_unique_constraint(
        "uq_source_captures_attempt", "source_captures", ["task_attempt_id"]
    )

    status_list = ", ".join(f"'{item}'" for item in ACTIVE)
    op.execute(
        sa.text(
            f"""
            INSERT INTO task_attempts (
                task_id, attempt_no, title, status, base_sha, forced_reason, branch_name,
                commit_sha, error, event_seq, execution_started_at, execution_finished_at,
                created_at, updated_at
            )
            SELECT
                id, 1, title, status, base_sha, forced_reason, branch_name,
                commit_sha, error, event_seq, created_at,
                CASE WHEN status IN ({status_list}) THEN NULL ELSE updated_at END,
                created_at, updated_at
            FROM tasks
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE tasks SET current_attempt_id = task_attempts.id
            FROM task_attempts
            WHERE task_attempts.task_id = tasks.id AND task_attempts.attempt_no = 1
            """
        )
    )
    for table in ("task_steps", "task_events", "source_captures", "change_sets", "test_runs"):
        op.execute(
            sa.text(
                f"""
                UPDATE {table} SET task_attempt_id = tasks.current_attempt_id
                FROM tasks
                WHERE {table}.task_id = tasks.id
                """
            )
        )


def downgrade() -> None:
    # Expand-first：回滚应用版本时保留扩展表，避免丢掉 Attempt 历史。
    return
