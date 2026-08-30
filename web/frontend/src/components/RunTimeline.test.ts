// @vitest-environment jsdom

import { createApp } from "vue";
import { afterEach, describe, expect, it } from "vitest";
import type { App } from "vue";
import type { HakoEvent, HakoEventType } from "../types/api";
import RunTimeline from "./RunTimeline.vue";

let eventId = 200;
let mountedApp: App<Element> | null = null;

function event(type: HakoEventType, payload: Record<string, unknown>): HakoEvent {
  eventId += 1;
  return {
    schemaVersion: "1.0",
    eventId,
    sessionId: "session-transcript",
    runId: "run-transcript",
    type,
    source: "WORKER",
    occurredAt: `2026-08-29T09:00:${String(eventId % 60).padStart(2, "0")}.000Z`,
    payload,
  };
}

function mountTimeline(events: HakoEvent[], active = false): HTMLElement {
  const host = document.createElement("div");
  document.body.appendChild(host);
  mountedApp = createApp(RunTimeline, { events, active });
  mountedApp.mount(host);
  return host;
}

afterEach(() => {
  mountedApp?.unmount();
  mountedApp = null;
  document.body.innerHTML = "";
});

describe("RunTimeline transcript hierarchy", () => {
  it("renders a conversation as a right-side user prompt and one open hako response", () => {
    const events = [
      event("run_started", {
        task: "江苏的省会是什么？",
        model: "deepseek-ai/DeepSeek-V4-Flash",
        cwd: "D:/demo",
      }),
      event("run_result", {
        success: true,
        stopReason: "done_read_only",
        finalText: "江苏省的省会是**南京**。",
        changedPaths: [],
        verification: [],
        steps: 1,
        totalTokens: 320,
      }),
    ];
    const host = mountTimeline(events);
    const turn = host.querySelector(".conversation-turn")!;
    const user = turn.querySelector(".user-message")!;
    const assistant = turn.querySelector(".assistant-message")!;

    expect(user.querySelector(".user-prompt-block")).not.toBeNull();
    expect(user.querySelector(".user-avatar")?.textContent).toBe("你");
    expect(assistant.querySelector(".assistant-avatar")?.textContent).toBe("h");
    expect(assistant.querySelector(".assistant-answer strong")?.textContent).toBe("南京");
    expect(assistant.querySelector(".tool-activity")).toBeNull();
    expect(user.compareDocumentPosition(assistant) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("nests tool activity and Verified Result inside the corresponding hako response", () => {
    const events = [
      event("run_started", { task: "修复失败测试并验证。", model: "test-model", cwd: "D:/demo" }),
      event("assistant_text", { text: "先读取失败位置，再做最小修改。" }),
      event("tool_call_started", {
        callId: "edit-1",
        name: "edit_file",
        args: { path: "checkout.py", old_text: "total > 180", new_text: "total >= 180" },
      }),
      event("tool_call_finished", {
        callId: "edit-1",
        name: "edit_file",
        ok: true,
        summary: "修改 checkout.py",
        modifiedPaths: ["checkout.py"],
      }),
      event("tool_call_started", {
        callId: "test-1",
        name: "run_command",
        args: { command: "pytest -q" },
      }),
      event("tool_call_finished", {
        callId: "test-1",
        name: "run_command",
        ok: true,
        summary: "12 passed",
        verificationKind: "test",
      }),
      event("run_result", {
        success: true,
        stopReason: "done_verified",
        finalText: "边界条件已修复。",
        changedPaths: ["checkout.py"],
        verification: [{ kind: "test", command: "pytest -q", summary: "12 passed", step: 3 }],
        steps: 3,
        totalTokens: 1800,
      }),
    ];
    const host = mountTimeline(events);
    const assistant = host.querySelector(".assistant-message")!;

    expect(assistant.querySelector(".assistant-activity-stream")).not.toBeNull();
    expect(assistant.querySelectorAll(".tool-activity")).toHaveLength(2);
    expect(assistant.querySelector(".verified-result")).not.toBeNull();
    expect(host.querySelector(".conversation-turn > .verified-result")).toBeNull();
  });

  it("collapses consecutive file reads into one expandable investigation step", () => {
    const events = [
      event("run_started", { task: "定位发布异常。", model: "test-model", cwd: "D:/demo" }),
      event("tool_call_started", {
        callId: "read-1",
        name: "read_file",
        args: { path: "app/services/publish_service.py" },
      }),
      event("tool_call_finished", {
        callId: "read-1",
        name: "read_file",
        ok: true,
        summary: "已读取 publish_service.py",
        touchedPaths: ["app/services/publish_service.py"],
      }),
      event("tool_call_started", {
        callId: "read-2",
        name: "read_file",
        args: { path: "app/repositories/campaign_repository.py" },
      }),
      event("tool_call_finished", {
        callId: "read-2",
        name: "read_file",
        ok: true,
        summary: "已读取 campaign_repository.py",
        touchedPaths: ["app/repositories/campaign_repository.py"],
      }),
      event("tool_call_started", {
        callId: "read-3",
        name: "read_file",
        args: { path: "app/repositories/uow.py" },
      }),
      event("tool_call_finished", {
        callId: "read-3",
        name: "read_file",
        ok: true,
        summary: "已读取 uow.py",
        touchedPaths: ["app/repositories/uow.py"],
      }),
      event("tool_call_started", {
        callId: "test-1",
        name: "run_command",
        args: { command: "python -m pytest -q" },
      }),
    ];
    const host = mountTimeline(events, true);
    const stream = host.querySelector(".assistant-activity-stream")!;

    expect(stream.querySelectorAll(":scope > .tool-activity")).toHaveLength(2);
    expect(stream.querySelector(".read-activity-group .event-title-row")?.textContent)
      .toContain("已读取 3 个文件");
    expect(stream.querySelectorAll(".read-file-item")).toHaveLength(3);
    expect(stream.textContent).toContain("app/repositories/campaign_repository.py");
  });

  it("keeps repository analysis and its folded trace inside the hako response", () => {
    const events = [
      event("run_started", { task: "分析死锁，不修改代码。", model: "test-model", cwd: "D:/demo" }),
      event("tool_call_started", {
        callId: "read-1",
        name: "read_file",
        args: { path: "scheduler.py" },
      }),
      event("tool_call_finished", {
        callId: "read-1",
        name: "read_file",
        ok: true,
        summary: "已读取 scheduler.py",
        touchedPaths: ["scheduler.py"],
      }),
      event("tool_call_started", {
        callId: "read-2",
        name: "read_file",
        args: { path: "worker.py" },
      }),
      event("tool_call_finished", {
        callId: "read-2",
        name: "read_file",
        ok: true,
        summary: "已读取 worker.py",
        touchedPaths: ["worker.py"],
      }),
      event("run_result", {
        success: true,
        stopReason: "done_read_only",
        finalText: "两个线程以相反顺序获取锁。",
        changedPaths: [],
        verification: [],
        steps: 2,
        totalTokens: 900,
      }),
    ];
    const host = mountTimeline(events);
    const assistant = host.querySelector(".assistant-message")!;

    expect(assistant.querySelector(".analysis-result")).not.toBeNull();
    expect(assistant.querySelector(".analysis-trace .read-activity-group")).not.toBeNull();
    expect(assistant.querySelectorAll(".analysis-trace .read-file-item")).toHaveLength(2);
    expect(assistant.querySelector(":scope > .assistant-content > .assistant-activity-stream")).toBeNull();
    expect(assistant.querySelector(".verified-result")).toBeNull();
  });
});
