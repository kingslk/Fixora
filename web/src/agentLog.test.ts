import { describe, expect, it } from "vitest";
import {
  describeAgentEvent,
  describeAgentEvents,
  formatAgentEvent,
  isActivityEvent,
  shortPath,
} from "./agentLog";

describe("agentLog", () => {
  it("formats tool and thought events", () => {
    expect(
      formatAgentEvent({
        seq: 1,
        type: "agent.tool",
        payload: { tool: "search_code", query: "accountLogoff", count: 4 },
      }),
    ).toBe("搜索 accountLogoff · 4 条");
    expect(
      formatAgentEvent({
        seq: 2,
        type: "agent.tool",
        payload: { tool: "list_files", path: "src", count: 12 },
      }),
    ).toBe("浏览 src · 12 项");
    expect(
      formatAgentEvent({
        seq: 3,
        type: "agent.thought",
        payload: { text: "先定位注销页" },
      }),
    ).toBe("先定位注销页");
  });

  it("filters activity events", () => {
    expect(isActivityEvent({ seq: 1, type: "agent.tool", payload: {} })).toBe(true);
    expect(isActivityEvent({ seq: 2, type: "agent.compact", payload: { trimmed: 3 } })).toBe(true);
    expect(isActivityEvent({ seq: 3, type: "task.started", payload: {} })).toBe(false);
    expect(formatAgentEvent({ seq: 4, type: "agent.compact", payload: { trimmed: 3 } })).toBe(
      "压缩旧工具上下文 · 3 段",
    );
    expect(
      formatAgentEvent({ seq: 5, type: "agent.compact", payload: { round: 2, trimmed: 12 } }),
    ).toBe("第 2 轮，重置上下文 · 12 步");
  });

  it("collapses long thoughts and shortens nested paths", () => {
    const text = "Let me start by exploring the relevant directory structure for the accountLogoff component and then read the Verify page.";
    const item = describeAgentEvent({ seq: 5, type: "agent.thought", payload: { text } });
    expect(item.title.endsWith("…")).toBe(true);
    expect(item.title.length).toBeLessThan(text.length);
    expect(item.detail).toBe(text);
    expect(shortPath("sdkApp/packages/component/src/components/AccountLogoff/Verify/index.tsx")).toBe(
      "…/AccountLogoff/Verify/index.tsx",
    );
    expect(
      describeAgentEvent({
        seq: 6,
        type: "agent.tool",
        payload: {
          tool: "search_code",
          query: "ic-clear",
          count: 2,
          preview: ["sdkApp/packages/component/src/components/AccountLogoff/Verify/index.tsx:187"],
        },
      }).detail,
    ).toContain("…/AccountLogoff/Verify/index.tsx:187");
  });

  it("aggregates compact events and labels phases", () => {
    const items = describeAgentEvents([
      { seq: 1, type: "agent.phase", payload: { phase: "locate" } },
      { seq: 2, type: "agent.tool", payload: { tool: "search_code", query: "ic-clear", count: 1 } },
      { seq: 3, type: "agent.compact", payload: { trimmed: 2 } },
      { seq: 4, type: "agent.compact", payload: { trimmed: 3 } },
      { seq: 5, type: "agent.phase", payload: { phase: "patch", files: ["src/Verify.tsx"] } },
      { seq: 6, type: "agent.tool", payload: { tool: "replace_in_file", path: "src/Verify.tsx", reason: "补按钮" } },
    ]);
    expect(items[0]).toMatchObject({ kind: "phase", title: "开始定位" });
    expect(items[2]).toMatchObject({ kind: "compact", meta: "5 段" });
    expect(items[3]).toMatchObject({ kind: "phase", title: "开始修改" });
    expect(items[4].title).toContain("Verify.tsx");
    expect(isActivityEvent({ seq: 1, type: "agent.phase", payload: {} })).toBe(true);
  });
});
