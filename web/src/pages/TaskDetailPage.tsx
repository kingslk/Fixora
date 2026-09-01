import {
  Beaker,
  CircleCheck,
  FileCode2,
  FileText,
  FolderTree,
  GitBranch,
  LocateFixed,
  MessageSquare,
  Minimize2,
  Pencil,
  RotateCcw,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { describeAgentEvents, type AgentActivity } from "../agentLog";
import { api, taskEventsUrl } from "../api";
import { Button, Loading, Notice } from "../components";
import type { ChangeSet, FileChange, Task, TaskEvent, TestRun } from "../types";

const statusLabels: Record<string, string> = {
  queued: "排队中",
  capturing_source: "读取页面",
  syncing_repository: "同步仓库",
  analyzing: "分析中",
  validating: "测试中",
  awaiting_approval: "等待确认",
  awaiting_force_approval: "未验证",
  committing: "正在提交",
  completed: "已提交",
  rejected: "已拒绝",
  failed: "失败",
  cancelled: "已取消",
  stale: "重新分析",
  superseded: "已替换",
};

const runningStatuses = new Set([
  "queued",
  "capturing_source",
  "syncing_repository",
  "analyzing",
  "validating",
  "committing",
  "stale",
]);

const feedbackOptions = [
  ["perfect", "完美修复"],
  ["partial", "部分修复"],
  ["incorrect", "修复错误"],
] as const;

const activityIcons: Record<string, typeof Search> = {
  thought: Sparkles,
  message: MessageSquare,
  compact: Minimize2,
  phase: LocateFixed,
  list_files: FolderTree,
  search_code: Search,
  read_file: FileText,
  apply_virtual_patch: Pencil,
  replace_in_file: Pencil,
  write_temp_test: Beaker,
};

function latestChangeSet(task: Task | null): ChangeSet | null {
  if (!task?.change_sets.length) return null;
  return [...task.change_sets].reverse().find((item) => item.status !== "superseded") ?? null;
}

function fileStats(file: FileChange): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const hunk of file.hunks) {
    for (const row of hunk.rows) {
      if (row.type === "insert") added += 1;
      if (row.type === "delete") removed += 1;
    }
  }
  return { added, removed };
}

function testStatusText(test: TestRun): string {
  if (test.status === "passed") return "临时脚本通过";
  if (test.status === "unverified") return "未验证";
  return "临时脚本未通过";
}

function ActivityItem({ item }: { item: AgentActivity }) {
  const [open, setOpen] = useState(false);
  const Icon = activityIcons[item.tool ?? ""] ?? activityIcons[item.kind] ?? FileCode2;
  const expandable = Boolean(item.detail);
  return (
    <li className={`activity-item activity-${item.kind}${item.tool ? ` tool-${item.tool}` : ""}`}>
      <span className="activity-dot" aria-hidden="true">
        <Icon size={13} strokeWidth={1.8} />
      </span>
      {expandable ? (
        <button type="button" className="activity-head" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
          <span className="activity-title">{item.title}</span>
          {item.meta ? <span className="activity-meta">{item.meta}</span> : null}
        </button>
      ) : (
        <div className="activity-head">
          <span className="activity-title">{item.title}</span>
          {item.meta ? <span className="activity-meta">{item.meta}</span> : null}
        </div>
      )}
      {open && item.detail ? <pre className="activity-detail">{item.detail}</pre> : null}
    </li>
  );
}

export function TaskDetailPage() {
  const { taskId } = useParams();
  const navigate = useNavigate();
  const id = Number(taskId);
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [selectedFile, setSelectedFile] = useState<FileChange | null>(null);
  const [error, setError] = useState("");
  const [forceReason, setForceReason] = useState("");
  const [feedbackRating, setFeedbackRating] = useState<"perfect" | "partial" | "incorrect" | "">("");
  const [feedbackReason, setFeedbackReason] = useState("");
  const [feedbackSaving, setFeedbackSaving] = useState(false);
  const [viewAttempt, setViewAttempt] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const refreshTimer = useRef<number | null>(null);

  const currentAttemptNo = task?.current_attempt_no ?? 1;
  const selectedAttemptNo = viewAttempt ?? currentAttemptNo;
  const viewingCurrent = selectedAttemptNo === currentAttemptNo;
  const historical = !viewingCurrent;

  const refresh = useCallback(async () => {
    try {
      const path =
        viewAttempt && viewAttempt !== currentAttemptNo
          ? `/tasks/${id}/attempts/${viewAttempt}`
          : `/tasks/${id}`;
      const next = await api<Task>(path);
      setTask(next);
      setFeedbackRating(next.feedback?.rating ?? "");
      setFeedbackReason(next.feedback?.reason ?? "");
      const changeSet = latestChangeSet(next);
      setSelectedFile((current) =>
        current ? changeSet?.files.find((item) => item.id === current.id) ?? changeSet?.files[0] ?? null : changeSet?.files[0] ?? null,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务加载失败");
    }
  }, [id, viewAttempt, currentAttemptNo]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const stream = new EventSource(taskEventsUrl(id));
    const eventTypes = [
      "task.started", "agent.tool", "agent.thought", "agent.message", "agent.compact", "agent.phase",
      "source.captured", "source.failed", "approval.required",
      "approval.approved", "approval.force_approved", "approval.rejected", "task.stale",
      "task.completed", "task.failed", "task.cancelled", "step.running", "step.completed",
      "step.failed", "step.waiting",
    ];
    const onEvent = (type: string) => (event: Event) => {
      const message = event as MessageEvent<string>;
      if (!viewingCurrent) return;
      setEvents((items) => [
        ...items.filter((item) => item.seq !== Number(message.lastEventId)),
        { seq: Number(message.lastEventId), type, payload: JSON.parse(message.data) as Record<string, unknown> },
      ].sort((a, b) => a.seq - b.seq));
      if (refreshTimer.current) window.clearTimeout(refreshTimer.current);
      refreshTimer.current = window.setTimeout(() => void refresh(), 250);
    };
    eventTypes.forEach((type) => stream.addEventListener(type, onEvent(type)));
    return () => {
      stream.close();
      if (refreshTimer.current) window.clearTimeout(refreshTimer.current);
    };
  }, [id, refresh, viewingCurrent]);

  useEffect(() => {
    if (viewingCurrent) return;
    void api<TaskEvent[]>(`/tasks/${id}/attempts/${selectedAttemptNo}/events`).then(setEvents).catch(() => setEvents([]));
  }, [id, selectedAttemptNo, viewingCurrent]);

  const changeSet = latestChangeSet(task);
  const lastTest = task?.test_runs.at(-1) ?? null;
  const activity = useMemo(() => describeAgentEvents(events), [events]);
  const running = Boolean(task && runningStatuses.has(task.status) && viewingCurrent);

  async function approval(action: "approve" | "force-approve") {
    if (!task || !changeSet || historical) return;
    setError("");
    try {
      await api(`/tasks/${task.id}/${action}`, {
        method: "POST",
        body: JSON.stringify({
          change_set_id: changeSet.id,
          patch_hash: changeSet.patch_hash,
          reason: action === "force-approve" ? forceReason : null,
        }),
      });
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "确认失败");
    }
  }

  async function reject() {
    if (!task || historical) return;
    await api(`/tasks/${task.id}/reject`, { method: "POST" });
    await refresh();
  }

  async function cancel() {
    if (!task || historical) return;
    await api(`/tasks/${task.id}/cancel`, { method: "POST" });
    await refresh();
  }

  async function rerun() {
    if (!task) return;
    setError("");
    try {
      const next = await api<Task>(`/tasks/${task.id}/rerun`, { method: "POST" });
      setViewAttempt(null);
      setTask(next);
      setEvents([]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重跑失败");
    }
  }

  async function remove() {
    if (!task) return;
    setError("");
    try {
      await api(`/tasks/${task.id}`, { method: "DELETE" });
      navigate("/tasks");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除失败");
      setConfirmDelete(false);
    }
  }

  async function saveFeedback() {
    if (!task || !feedbackRating || (feedbackRating === "incorrect" && feedbackReason.trim().length < 3)) return;
    setFeedbackSaving(true);
    setError("");
    try {
      await api(`/tasks/${task.id}/attempts/${task.attempt_no}/feedback`, {
        method: "PUT",
        body: JSON.stringify({ rating: feedbackRating, reason: feedbackReason.trim() }),
      });
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "评价保存失败");
    } finally {
      setFeedbackSaving(false);
    }
  }

  if (!task) return error ? <Notice tone="error">{error}</Notice> : <Loading label="读取任务" />;
  const canApprove = viewingCurrent && task.status === "awaiting_approval";
  const canForce = viewingCurrent && task.status === "awaiting_force_approval";
  const canRerun = !runningStatuses.has(task.status);
  const canDelete = Boolean(task.attempts.find((item) => item.attempt_no === currentAttemptNo)?.execution_finished_at) && !runningStatuses.has(task.status);

  return (
    <div className="task-detail">
      <header className="task-header">
        <button className="back-button" onClick={() => navigate("/tasks")} aria-label="返回任务列表">←</button>
        <div className="task-header-main">
          <h1>#{task.id} {task.title}</h1>
          <p>
            {task.repository.path_with_namespace}
            <span>·</span>
            {task.repository.default_branch}
            <span>·</span>
            <em className={`status-text status-${task.status}`}>{statusLabels[task.status] ?? task.status}</em>
          </p>
        </div>
        <div className="task-header-actions">
          {task.attempts.length > 1 ? (
            <label className="attempt-select">
              Attempt
              <select
                aria-label="选择 Attempt"
                value={selectedAttemptNo}
                onChange={(event) => setViewAttempt(Number(event.target.value))}
              >
                {task.attempts.map((item) => (
                  <option key={item.attempt_no} value={item.attempt_no}>
                    #{item.attempt_no} {statusLabels[item.status] ?? item.status}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {canRerun ? (
            <Button onClick={() => void rerun()}>
              <RotateCcw size={16} />
              重跑
            </Button>
          ) : null}
          {running && viewingCurrent ? (
            <Button onClick={() => void cancel()}>取消</Button>
          ) : null}
          {canDelete ? (
            <Button variant="danger" onClick={() => setConfirmDelete(true)}>
              <Trash2 size={16} />
              删除
            </Button>
          ) : null}
        </div>
      </header>

      {confirmDelete ? (
        <div className="modal-backdrop">
          <div className="modal">
            <header><h2>删除任务 #{task.id}</h2></header>
            <p>本地记录不可恢复，GitLab 分支、commit 和 comment 不受影响。共享仓库缓存也不会删除。</p>
            <div className="modal-actions">
              <Button onClick={() => setConfirmDelete(false)}>取消</Button>
              <Button variant="danger" onClick={() => void remove()}>确认删除</Button>
            </div>
          </div>
        </div>
      ) : null}

      {running ? (
        <section className="task-running page-scroll">
          <p className="running-phase">{statusLabels[task.status] ?? task.status}</p>
          {task.image_url ? <img className="task-running-image" src={task.image_url} alt={task.image_name || "问题截图"} /> : null}
          <ol className="activity-log">
            {activity.slice(-8).map((item) =>
              item.kind === "phase" ? (
                <li className="activity-phase" key={item.seq}><h3>{item.title}</h3></li>
              ) : (
                <ActivityItem item={item} key={item.seq} />
              ),
            )}
          </ol>
        </section>
      ) : (
        <div className="task-result-layout page-scroll">
          <section className="task-diff-main">
            {changeSet ? (
              <>
                <ul className="result-files">
                  {changeSet.files.map((file) => {
                    const stats = fileStats(file);
                    return (
                      <li key={file.id}>
                        <button type="button" className={selectedFile?.id === file.id ? "selected" : ""} onClick={() => setSelectedFile(file)}>
                          <FileCode2 size={16} />
                          <span>{file.path}</span>
                          <em>+{stats.added} −{stats.removed}</em>
                        </button>
                      </li>
                    );
                  })}
                </ul>
                {selectedFile ? (
                  <div className="diff-view" role="table" aria-label={`${selectedFile.path} 修改内容`}>
                    {selectedFile.hunks.map((hunk, hunkIndex) => (
                      <div className="diff-hunk" key={`${hunk.header}-${hunkIndex}`}>
                        <div className="diff-hunk-header">{hunk.header}</div>
                        {hunk.rows.map((row, rowIndex) => (
                          <div className={`diff-row ${row.type}`} key={`${rowIndex}-${row.old}-${row.new}`} role="row">
                            <span>{row.old ?? ""}</span><span>{row.new ?? ""}</span><code>{row.type === "insert" ? "+ " : row.type === "delete" ? "- " : "  "}{row.text}</code>
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="activity-empty">选择一个文件查看 diff。</p>
                )}
              </>
            ) : (
              <p className="activity-empty">还没有修改结果。</p>
            )}
          </section>
          <aside className="task-result-sidebar">
            {changeSet?.root_cause ? (
              <section>
                <h2>根因</h2>
                <p>{changeSet.root_cause}</p>
              </section>
            ) : null}
            {changeSet?.summary ? (
              <section>
                <h2>修改摘要</h2>
                <p>{changeSet.summary}</p>
              </section>
            ) : null}
            {lastTest ? (
              <section>
                <h2>验证结果</h2>
                <p className={lastTest.status === "passed" ? "success" : "danger"}>
                  <CircleCheck size={16} />
                  {testStatusText(lastTest)}
                </p>
                {lastTest.command.length ? <code>{lastTest.command.join(" ")}</code> : null}
                {lastTest.output ? <pre>{lastTest.output.slice(-2_000)}</pre> : null}
              </section>
            ) : null}
            {task.status === "completed" && task.branch_name ? (
              <section>
                <h2>已提交</h2>
                <p><GitBranch size={16} /> {task.branch_name}</p>
                <p>{task.commit_sha?.slice(0, 12)}</p>
              </section>
            ) : null}
            {task.image_url ? (
              <section>
                <h2>问题截图</h2>
                <img className="task-input-image" src={task.image_url} alt={task.image_name || "问题截图"} />
              </section>
            ) : null}
            {!runningStatuses.has(task.status) ? (
              <section className="task-feedback">
                <h2>修复评价</h2>
                <div className="feedback-options" role="radiogroup" aria-label="修复评价">
                  {feedbackOptions.map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      className={feedbackRating === value ? "selected" : ""}
                      aria-pressed={feedbackRating === value}
                      onClick={() => setFeedbackRating(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <textarea
                  value={feedbackReason}
                  onChange={(event) => setFeedbackReason(event.target.value)}
                  placeholder="补充真实评价；选择“修复错误”时必填具体原因"
                  aria-label="评价补充说明"
                  rows={3}
                />
                <Button
                  variant="secondary"
                  disabled={!feedbackRating || feedbackSaving || (feedbackRating === "incorrect" && feedbackReason.trim().length < 3)}
                  onClick={() => void saveFeedback()}
                >
                  {feedbackSaving ? "保存中…" : task.feedback ? "更新评价" : "提交评价"}
                </Button>
              </section>
            ) : null}
            {task.status === "failed" && task.error ? <Notice tone="error">{task.error}</Notice> : null}
            {error ? <Notice tone="error">{error}</Notice> : null}
            {(canApprove || canForce) && changeSet ? (
              <div className="approval-panel">
                {canForce ? (
                  <input value={forceReason} onChange={(event) => setForceReason(event.target.value)} placeholder="填写强制提交原因" aria-label="强制提交原因" />
                ) : null}
                <Button variant="primary" disabled={canForce && !forceReason.trim()} onClick={() => void approval(canForce ? "force-approve" : "approve")}>
                  {canForce ? "强制创建分支" : "确认并创建分支"}
                </Button>
                <button className="text-action" onClick={() => void reject()}>拒绝</button>
              </div>
            ) : null}
            {historical ? <p className="activity-empty">历史 Attempt 只读。</p> : null}
            {task.status === "failed" && viewingCurrent ? (
              <Button variant="primary" onClick={() => void rerun()}><RotateCcw size={16} />重跑</Button>
            ) : null}
          </aside>
        </div>
      )}

      <details className="activity-feed">
        <summary>执行过程{activity.length ? ` · ${activity.length} 步` : ""}</summary>
        {activity.length ? (
          <ol className="activity-log">
            {activity.map((item) =>
              item.kind === "phase" ? (
                <li className="activity-phase" key={item.seq}>
                  <h3>{item.title}</h3>
                  {item.meta ? <span>{item.meta}</span> : null}
                </li>
              ) : (
                <ActivityItem item={item} key={item.seq} />
              ),
            )}
          </ol>
        ) : (
          <p className="activity-empty">等待 Agent 开始定位代码。</p>
        )}
      </details>
    </div>
  );
}
