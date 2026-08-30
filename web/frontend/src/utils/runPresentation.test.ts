import { describe, expect, it } from "vitest";
import type { HakoEvent, HakoEventType, StopReason } from "../types/api";
import { deriveRunPresentation } from "./runPresentation";
import { statusPresentation } from "./presentation";

let eventId = 0;

function event(type: HakoEventType, payload: Record<string, unknown>): HakoEvent {
  eventId += 1;
  return {
    schemaVersion: "1.0",
    eventId,
    sessionId: "session-1",
    runId: "run-1",
    type,
    source: "WORKER",
    occurredAt: "2026-08-29T08:00:00.000Z",
    payload,
  };
}

function result(
  stopReason: StopReason | null,
  options: {
    success?: boolean;
    changedPaths?: string[];
    verification?: Array<{ kind: string; command: string; summary: string; step: number }>;
    finalText?: string;
  } = {},
): HakoEvent {
  return event("run_result", {
    success: options.success ?? ["done_read_only", "done_verified"].includes(stopReason ?? ""),
    stopReason,
    changedPaths: options.changedPaths ?? [],
    verification: options.verification ?? [],
    finalText: options.finalText ?? "结果",
    steps: 3,
    totalTokens: 1200,
  });
}

describe("deriveRunPresentation", () => {
  it("renders a tool-free DONE_READ_ONLY run as conversation", () => {
    const completed = result("done_read_only", { finalText: "南京。" });
    const view = deriveRunPresentation(completed, [completed]);

    expect(view.kind).toBe("conversation");
    expect(view.hasWorkspaceToolActivity).toBe(false);
    expect(view.changedPaths).toEqual([]);
  });

  it("renders a read-only workspace investigation as repository analysis", () => {
    const events = [
      event("tool_call_started", { callId: "a", name: "read_file", args: { path: "scheduler.py" } }),
      event("tool_call_finished", { callId: "a", name: "read_file", ok: true, touchedPaths: ["scheduler.py"] }),
      event("tool_call_started", { callId: "b", name: "read_file", args: { path: "worker.py" } }),
      event("tool_call_finished", { callId: "b", name: "read_file", ok: true, touchedPaths: ["worker.py"] }),
    ];
    const completed = result("done_read_only", { finalText: "锁顺序相反导致死锁。" });
    events.push(completed);

    const view = deriveRunPresentation(completed, events);
    expect(view.kind).toBe("analysis");
    expect(view.investigatedPaths).toEqual(["scheduler.py", "worker.py"]);
  });

  it("requires changed paths and real verification for a verified change", () => {
    const verification = [{
      kind: "test",
      command: "python -m pytest -q",
      summary: "12 passed",
      step: 4,
    }];
    const completed = result("done_verified", {
      changedPaths: ["checkout.py", "pricing.py"],
      verification,
    });
    const view = deriveRunPresentation(completed, [completed]);

    expect(view.kind).toBe("verified_change");
    expect(view.verification).toEqual(verification);
  });

  it("does not trust DONE_VERIFIED when its evidence payload is incomplete", () => {
    const completed = result("done_verified", { changedPaths: ["checkout.py"] });
    expect(deriveRunPresentation(completed, [completed]).kind).toBe("unverified");
  });

  it.each([
    ["done_unverified", "unverified"],
    ["cancelled", "cancelled"],
    ["denied", "denied"],
    ["error", "error"],
    ["incomplete", "incomplete"],
  ] as const)("keeps %s out of successful presentation", (reason, expected) => {
    const completed = result(reason, { success: false, changedPaths: ["partial.py"] });
    expect(deriveRunPresentation(completed, [completed]).kind).toBe(expected);
  });

  it("recognizes CANCELLED even when the cancelled summary has no stop reason", () => {
    const status = event("run_status", { current: "CANCELLED" });
    const completed = result(null, { success: false });
    expect(deriveRunPresentation(completed, [status, completed]).kind).toBe("cancelled");
  });

  it("does not label every completed run as verified in the header", () => {
    expect(statusPresentation("COMPLETED", "done_read_only")).toEqual({
      label: "已完成",
      tone: "neutral",
    });
    expect(statusPresentation("COMPLETED", "done_verified")).toEqual({
      label: "已验证完成",
      tone: "success",
    });
  });
});
