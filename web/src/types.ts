export type ApiMode = "responses" | "chat_completions";

export type RuntimeProfile = {
  language: "node" | "python";
  runtime_version: string;
  package_manager: string;
  working_directory: string;
  install_argv: string[];
  test_argv: string[];
  lockfile_path: string | null;
  lockfile_hash: string | null;
};

export type Repository = {
  id: number;
  gitlab_project_id: number;
  name: string;
  path_with_namespace: string;
  default_branch: string;
  cached_sha: string | null;
  cache_status: string;
  last_fetch_at: string | null;
  runtime_profile: RuntimeProfile | null;
};

export type DiffRow = {
  type: "context" | "insert" | "delete";
  old: number | null;
  new: number | null;
  text: string;
};

export type FileChange = {
  id: number;
  path: string;
  reason: string;
  hunks: Array<{ header: string; rows: DiffRow[] }>;
};

export type ChangeSet = {
  id: number;
  base_sha: string;
  patch_hash: string;
  summary: string;
  root_cause: string;
  status: string;
  files: FileChange[];
};

export type TestRun = {
  id: number;
  attempt: number;
  status: string;
  command: string[];
  exit_code: number | null;
  output: string;
  duration_ms: number | null;
};

export type TaskAttemptSummary = {
  attempt_no: number;
  title: string;
  status: string;
  branch_name: string | null;
  commit_sha: string | null;
  error: string | null;
  created_at: string;
  execution_finished_at: string | null;
};

export type TaskFeedback = {
  rating: "perfect" | "partial" | "incorrect";
  reason: string;
  submitted_at: string;
};

export type Task = {
  id: number;
  repository_id: number;
  title: string;
  description: string;
  source_url: string | null;
  image_name: string | null;
  image_mime: string | null;
  image_size: number | null;
  image_url: string | null;
  status: string;
  base_sha: string | null;
  branch_name: string | null;
  commit_sha: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  repository: Repository;
  change_sets: ChangeSet[];
  test_runs: TestRun[];
  /** 本响应对应的 Attempt */
  attempt_no: number;
  /** Task 当前指向的 Attempt；历史查看时两者会不同 */
  current_attempt_no: number;
  attempts: TaskAttemptSummary[];
  feedback: TaskFeedback | null;
};

export type TaskEvent = {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  created_at?: string;
};

export type SettingsStatus = {
  configured: boolean;
  values: Record<string, unknown>;
};

export type BrowserAuthProfile = {
  id: number;
  origin: string;
  kind: string;
  updated_at: string;
};
