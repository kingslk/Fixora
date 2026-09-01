import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

describe("api", () => {
  it("surfaces backend detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "配置无效" }), { status: 400, headers: { "Content-Type": "application/json" } })));
    await expect(api("/settings/model")).rejects.toEqual(new ApiError("配置无效", 400));
    vi.unstubAllGlobals();
  });
});

