"""Initial Fixora schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "repositories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gitlab_project_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("path_with_namespace", sa.String(512), nullable=False),
        sa.Column("clone_url", sa.String(2048), nullable=False),
        sa.Column("default_branch", sa.String(255), nullable=False),
        sa.Column("cached_sha", sa.String(64)),
        sa.Column("cache_status", sa.String(32), nullable=False),
        sa.Column("last_fetch_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("gitlab_project_id"),
        sa.UniqueConstraint("path_with_namespace"),
    )
    op.create_index("ix_repositories_gitlab_project_id", "repositories", ["gitlab_project_id"])
    op.create_table(
        "repository_runtime_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "repository_id",
            sa.Integer(),
            sa.ForeignKey("repositories.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("runtime_version", sa.String(64), nullable=False),
        sa.Column("package_manager", sa.String(32), nullable=False),
        sa.Column("working_directory", sa.String(1024), nullable=False),
        sa.Column("install_argv", sa.JSON(), nullable=False),
        sa.Column("test_argv", sa.JSON(), nullable=False),
        sa.Column("lockfile_path", sa.String(1024)),
        sa.Column("lockfile_hash", sa.String(64)),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(4096)),
        sa.Column("status", sa.String(48), nullable=False),
        sa.Column("base_sha", sa.String(64)),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("forced_reason", sa.Text()),
        sa.Column("branch_name", sa.String(255)),
        sa.Column("commit_sha", sa.String(64)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tasks_repository_id", "tasks", ["repository_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_table(
        "task_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("task_id", "position"),
    )
    op.create_index("ix_task_steps_task_id", "task_steps", ["task_id"])
    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "seq"),
    )
    op.create_index("ix_task_events_task_id", "task_events", ["task_id"])
    op.create_table(
        "source_captures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("requested_url", sa.String(4096), nullable=False),
        sa.Column("final_url", sa.String(4096)),
        sa.Column("title", sa.String(1024)),
        sa.Column("text_content", sa.Text()),
        sa.Column("screenshot_path", sa.String(4096)),
        sa.Column("insecure_http", sa.Boolean(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "change_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("base_sha", sa.String(64), nullable=False),
        sa.Column("patch_hash", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_change_sets_task_id", "change_sets", ["task_id"])
    op.create_index("ix_change_sets_patch_hash", "change_sets", ["patch_hash"])
    op.create_table(
        "file_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("change_set_id", sa.Integer(), sa.ForeignKey("change_sets.id"), nullable=False),
        sa.Column("path", sa.String(2048), nullable=False),
        sa.Column("base_blob_sha", sa.String(64), nullable=False),
        sa.Column("old_content", sa.Text(), nullable=False),
        sa.Column("new_content", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("unified_diff", sa.Text(), nullable=False),
        sa.Column("hunks", sa.JSON(), nullable=False),
        sa.UniqueConstraint("change_set_id", "path"),
    )
    op.create_index("ix_file_changes_change_set_id", "file_changes", ["change_set_id"])
    op.create_table(
        "test_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("change_set_id", sa.Integer(), sa.ForeignKey("change_sets.id")),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("command", sa.JSON(), nullable=False),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("output", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_test_runs_task_id", "test_runs", ["task_id"])
    op.create_table(
        "browser_auth_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("origin", sa.String(2048), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("encrypted_state", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("browser_auth_profiles")
    op.drop_index("ix_test_runs_task_id", table_name="test_runs")
    op.drop_table("test_runs")
    op.drop_index("ix_file_changes_change_set_id", table_name="file_changes")
    op.drop_table("file_changes")
    op.drop_index("ix_change_sets_patch_hash", table_name="change_sets")
    op.drop_index("ix_change_sets_task_id", table_name="change_sets")
    op.drop_table("change_sets")
    op.drop_table("source_captures")
    op.drop_index("ix_task_events_task_id", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("ix_task_steps_task_id", table_name="task_steps")
    op.drop_table("task_steps")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_repository_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("repository_runtime_profiles")
    op.drop_index("ix_repositories_gitlab_project_id", table_name="repositories")
    op.drop_table("repositories")
    op.drop_table("system_settings")
