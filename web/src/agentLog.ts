import type { TaskEvent } from "./types";

export type AgentActivity = {
  seq: number;
  kind: "thought" | "message" | "tool" | "compact" | "phase";
  tool?: string;
  phase?: string;
  title: string;
  meta?: string;
  detail?: string;
};

export function isActivityEvent(event: TaskEvent): boolean {
  return (
    event.type === "agent.tool" ||
    event.type === "agent.thought" ||
    event.type === "agent.message" ||
    event.type === "agent.compact" ||
    event.type === "agent.phase"
  );
}

// agent.compact 只兼容旧任务；新流程改为阶段重建会话，不再发这个事件。

export function describeAgentEvents(events: TaskEvent[]): AgentActivity[] {
  const items: AgentActivity[] = [];
  let compactSeq = 0;
  let compactTrimmed = 0;
  const flushCompact = () => {
    if (!compactTrimmed) return;
    items.push({
      seq: compactSeq,
      kind: "compact",
      title: "压缩旧工具上下文",
      meta: `${compactTrimmed} 段`,
    });
    compactTrimmed = 0;
  };
  for (const event of events) {
    if (!isActivityEvent(event)) continue;
    if (event.type === "agent.compact") {
      compactSeq = event.seq;
      compactTrimmed += Number(event.payload.trimmed ?? 1);
      continue;
    }
    flushCompact();
    items.push(describeAgentEvent(event));
  }
  flushCompact();
  return items;
}

export function describeAgentEvent(event: TaskEvent): AgentActivity {
  const payload = event.payload;
  if (event.type === "agent.phase") {
    const phase = String(payload.phase ?? "locate");
    return {
      seq: event.seq,
      kind: "phase",
      phase,
      title: phase === "patch" ? "开始修改" : "开始定位",
      meta: Array.isArray(payload.files) ? `${payload.files.length} 个文件` : undefined,
    };
  }
  if (event.type === "agent.thought") {
    const text = String(payload.text ?? "思考中").trim();
    const title = summarizeText(text);
    return { seq: event.seq, kind: "thought", title, detail: text === title ? undefined : text };
  }
  if (event.type === "agent.message") {
    const text = String(payload.text ?? "模型输出").trim();
    const title = summarizeText(text);
    return { seq: event.seq, kind: "message", title, detail: text === title ? undefined : text };
  }
  if (event.type === "agent.compact") {
    const round = Number(payload.round ?? 0);
    if (round) {
      return {
        seq: event.seq,
        kind: "compact",
        title: `第 ${round} 轮，重置上下文`,
        meta: Number(payload.trimmed ?? 0) ? `${Number(payload.trimmed)} 步` : undefined,
      };
    }
    return {
      seq: event.seq,
      kind: "compact",
      title: "压缩旧工具上下文",
      meta: `${Number(payload.trimmed ?? 0)} 段`,
    };
  }
  const tool = String(payload.tool ?? "tool");
  if (tool === "list_files") {
    return {
      seq: event.seq,
      kind: "tool",
      tool,
      title: `浏览 ${shortPath(String(payload.path ?? "."))}`,
      meta: `${Number(payload.count ?? 0)} 项`,
      detail: previewText(payload),
    };
  }
  if (tool === "search_code") {
    return {
      seq: event.seq,
      kind: "tool",
      tool,
      title: `搜索 ${String(payload.query ?? "")}`,
      meta: `${Number(payload.count ?? 0)} 条`,
      detail: previewText(payload),
    };
  }
  if (tool === "read_file") {
    return {
      seq: event.seq,
      kind: "tool",
      tool,
      title: `读取 ${shortPath(String(payload.path ?? ""))}`,
      meta: payload.start != null && payload.end != null ? `${payload.start}–${payload.end}` : undefined,
    };
  }
  if (tool === "apply_virtual_patch" || tool === "replace_in_file") {
    return {
      seq: event.seq,
      kind: "tool",
      tool,
      title: `修改 ${shortPath(String(payload.path ?? ""))}`,
      detail: String(payload.reason ?? "").trim() || undefined,
    };
  }
  if (tool === "write_temp_test") {
    return {
      seq: event.seq,
      kind: "tool",
      tool,
      title: `写入临时测试 ${shortPath(String(payload.path ?? ""))}`,
    };
  }
  return { seq: event.seq, kind: "tool", tool, title: tool, detail: previewText(payload) };
}

export function formatAgentEvent(event: TaskEvent): string {
  const item = describeAgentEvent(event);
  return item.meta ? `${item.title} · ${item.meta}` : item.title;
}

export function shortPath(path: string): string {
  const parts = path.split("/").filter(Boolean);
  if (parts.length <= 3) return path || ".";
  return `…/${parts.slice(-3).join("/")}`;
}

function summarizeText(text: string, max = 88): string {
  const compact = text.replace(/\s+/g, " ").trim();
  if (compact.length <= max) return compact;
  const cut = compact.slice(0, max);
  const boundary = cut.lastIndexOf(" ");
  return `${(boundary > 40 ? cut.slice(0, boundary) : cut).trim()}…`;
}

function previewText(payload: Record<string, unknown>): string | undefined {
  const preview = payload.preview;
  if (Array.isArray(preview) && preview.length) {
    return preview.map((item) => shortPath(String(item))).join("\n");
  }
  if (typeof preview === "string" && preview.trim()) return preview.trim();
  return undefined;
}
