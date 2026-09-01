from __future__ import annotations

import os

os.environ.setdefault("FIXORA_SECRET_KEY", "test-secret-key")
os.environ.setdefault("FIXORA_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("FIXORA_DATA_ROOT", "/tmp/fixora-test-data")
os.environ.setdefault("FIXORA_SYSTEMD_RUNNER_ENABLED", "false")
