import { ImagePlus, Link2, Play, RefreshCcw, X } from "lucide-react";
import { ClipboardEvent, FormEvent, useRef, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Button, Loading, Notice, formatTime } from "../components";
import type { Repository, Task } from "../types";

const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

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
};

export function TasksPage() {
  const navigate = useNavigate();
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [description, setDescription] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [showLink, setShowLink] = useState(false);
  const [imageDataUrl, setImageDataUrl] = useState("");
  const [imageName, setImageName] = useState("");
  const [repositoryId, setRepositoryId] = useState<number | "">("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const imageInputRef = useRef<HTMLInputElement>(null);

  async function attachImage(file: File | null) {
    if (!file) return;
    if (!IMAGE_TYPES.has(file.type)) {
      setError("仅支持 PNG、JPEG、WebP 图片");
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setError("图片不能超过 8 MB");
      return;
    }
    const dataUrl = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error("图片读取失败"));
      reader.readAsDataURL(file);
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "图片读取失败");
      return "";
    });
    if (!dataUrl) return;
    setError("");
    setImageDataUrl(dataUrl);
    setImageName(file.name || "screenshot");
  }

  function pasteImage(event: ClipboardEvent<HTMLTextAreaElement>) {
    const imageItem = Array.from(event.clipboardData.items).find(
      (item) => item.kind === "file" && IMAGE_TYPES.has(item.type),
    );
    const file = imageItem?.getAsFile();
    if (!file) return;
    event.preventDefault();
    void attachImage(file);
  }

  useEffect(() => {
    let active = true;
    Promise.all([api<Repository[]>("/repositories"), api<Task[]>("/tasks")])
      .then(([repositoryItems, taskItems]) => {
        if (!active) return;
        setRepositories(repositoryItems);
        setTasks(taskItems);
        setRepositoryId(repositoryItems[0]?.id ?? "");
      })
      .catch((reason: Error) => active && setError(reason.message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!repositoryId || (description.trim().length < 3 && !imageDataUrl)) return;
    setSubmitting(true);
    setError("");
    try {
      const task = await api<Task>("/tasks", {
        method: "POST",
        body: JSON.stringify({
          repository_id: repositoryId,
          description: description.trim(),
          source_url: sourceUrl.trim() || null,
          image_data_url: imageDataUrl || null,
          image_name: imageName || null,
        }),
      });
      navigate(`/tasks/${task.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建任务失败");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <Loading label="读取任务" />;

  return (
    <div className="tasks-page page-scroll">
      <section className="task-create-section">
        <h1>修复一个问题</h1>
        <form className="task-composer" onSubmit={submit}>
          <textarea
            aria-label="问题描述"
            placeholder="描述问题，或粘贴问题截图…"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            onPaste={pasteImage}
          />
          {imageDataUrl ? (
            <div className="task-image-attachment">
              <img src={imageDataUrl} alt="问题截图预览" />
              <span title={imageName}>{imageName || "问题截图"}</span>
              <button
                type="button"
                className="icon-button"
                aria-label="移除问题截图"
                onClick={() => { setImageDataUrl(""); setImageName(""); }}
              >
                <X size={16} />
              </button>
            </div>
          ) : null}
          {showLink ? (
            <input
              className="source-url-input"
              type="url"
              placeholder="https://…"
              aria-label="问题链接"
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
              autoFocus
            />
          ) : null}
          <div className="composer-actions">
            <div className="composer-attachments">
              <input
                ref={imageInputRef}
                className="visually-hidden"
                type="file"
                accept="image/png,image/jpeg,image/webp"
                aria-label="选择问题截图"
                onChange={(event) => {
                  void attachImage(event.target.files?.[0] ?? null);
                  event.target.value = "";
                }}
              />
              <button type="button" className="icon-button" onClick={() => imageInputRef.current?.click()} aria-label="添加问题截图">
                <ImagePlus size={19} />
              </button>
              <button
                type="button"
                className={`icon-button ${showLink ? "selected" : ""}`}
                onClick={() => setShowLink((value) => !value)}
                aria-label="添加问题链接"
              >
                <Link2 size={19} />
              </button>
              <span className="attachment-hint">可粘贴截图或选择文件</span>
            </div>
            <div className="composer-controls">
              <select
                aria-label="代码仓库"
                value={repositoryId}
                onChange={(event) => setRepositoryId(Number(event.target.value))}
              >
                {repositories.map((repository) => (
                  <option key={repository.id} value={repository.id}>
                    {repository.path_with_namespace}
                  </option>
                ))}
              </select>
              <span className="branch-hint">
                基于默认分支 {repositories.find((item) => item.id === repositoryId)?.default_branch ?? "—"}
              </span>
              <Button variant="primary" disabled={submitting || !repositoryId || (description.trim().length < 3 && !imageDataUrl)}>
                {submitting ? <RefreshCcw size={16} className="spin" /> : <Play size={16} fill="currentColor" />}
                开始分析
              </Button>
            </div>
          </div>
        </form>
        {repositories.length === 0 ? (
          <Notice tone="warning">尚未添加仓库。先到“仓库”页面连接 GitLab 项目。</Notice>
        ) : null}
        {error ? <Notice tone="error">{error}</Notice> : null}
      </section>

      <section className="recent-tasks">
        <h2>最近任务</h2>
        {tasks.length === 0 ? (
          <div className="empty-row">暂无任务，描述一个问题开始。</div>
        ) : (
          <div className="task-table">
            <div className="task-row task-table-head">
              <span>ID</span><span>标题</span><span>仓库</span><span>状态</span><span>更新时间</span>
            </div>
            {tasks.map((task) => (
              <button key={task.id} className="task-row" onClick={() => navigate(`/tasks/${task.id}`)}>
                <span className="task-id">TASK-{task.id}</span>
                <strong>{task.title}</strong>
                <span>{task.repository.path_with_namespace}</span>
                <span className={`status-text status-${task.status}`}>{statusLabels[task.status] ?? task.status}</span>
                <span>{formatTime(task.updated_at)}</span>
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
