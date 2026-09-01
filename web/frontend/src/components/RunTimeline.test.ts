// @vitest-environment jsdom

import { createApp, h, nextTick, ref } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
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
  vi.useRealTimers();
});

describe("RunTimeline transcript hierarchy", () => {
  it("throttles tail scrolling and stops following when the user scrolls upward", async () => {
    vi.useFakeTimers();
    const liveEvents = ref<HakoEvent[]>([
      event("run_started", { task: "检查仓库", model: "test-model", cwd: "D:/demo" }),
    ]);
    const host = document.createElement("div");
    document.body.appendChild(host);
    mountedApp = createApp({
      setup() {
        return () => h(RunTimeline, { events: liveEvents.value, active: true });
      },
    });
    mountedApp.mount(host);
    await nextTick();

    const scroll = host.querySelector<HTMLElement>(".timeline-scroll")!;
    let scrollHeight = 1000;
    let scrollTop = 0;
    let programmaticScrolls = 0;
    Object.defineProperties(scroll, {
      scrollHeight: { configurable: true, get: () => scrollHeight },
      clientHeight: { configurable: true, get: () => 200 },
      scrollTop: {
        configurable: true,
        get: () => scrollTop,
        set: (value: number) => {
          scrollTop = value;
          programmaticScrolls += 1;
        },
      },
    });

    await vi.advanceTimersByTimeAsync(80);
    expect(scrollTop).toBe(1000);
    programmaticScrolls = 0;

    liveEvents.value.push(
      event("assistant_text", { text: "正在读取文件。" }),
      event("turn_started", { step: 2, maxSteps: 10 }),
    );
    await nextTick();
    await vi.advanceTimersByTimeAsync(79);
    expect(programmaticScrolls).toBe(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(programmaticScrolls).toBe(1);

    scrollTop = 300;
    scroll.dispatchEvent(new WheelEvent("wheel", { deltaY: -120 }));
    scroll.dispatchEvent(new Event("scroll"));
    liveEvents.value.push(event("assistant_text", { text: "继续分析。" }));
    await nextTick();
    await vi.advanceTimersByTimeAsync(100);
    expect(scrollTop).toBe(300);

    scrollTop = 800;
    scroll.dispatchEvent(new Event("scroll"));
    scrollHeight = 1200;
    liveEvents.value.push(event("assistant_text", { text: "分析完成。" }));
    await nextTick();
    await vi.advanceTimersByTimeAsync(80);
    expect(scrollTop).toBe(1200);
  });

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
        args: { file_path: "app/services/publish_service.py" },
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
      .toContain("读取 3 个相关文件");
    expect(stream.querySelectorAll(".read-file-item")).toHaveLength(3);
    expect(stream.textContent).toContain("app/repositories/campaign_repository.py");
  });

  it("collapses alternating list and read calls into one exploration milestone", () => {
    const events = [
      event("run_started", { task: "定位发布异常。", model: "test-model", cwd: "D:/demo" }),
      event("tool_call_started", {
        callId: "list-1",
        name: "list_dir",
        args: { path: "." },
      }),
      event("tool_call_finished", {
        callId: "list-1",
        name: "list_dir",
        ok: true,
        summary: "./ 共 5 项",
      }),
      event("tool_call_started", {
        callId: "list-2",
        name: "list_dir",
        args: { path: "app" },
      }),
      event("tool_call_finished", {
        callId: "list-2",
        name: "list_dir",
        ok: true,
        summary: "app 共 6 项",
      }),
      event("tool_call_started", {
        callId: "read-1",
        name: "read_file",
        args: { path: "README.md" },
      }),
    ];
    const host = mountTimeline(events, true);
    const stream = host.querySelector(".assistant-activity-stream")!;

    expect(stream.querySelectorAll(":scope > .tool-activity")).toHaveLength(1);
    expect(stream.querySelector(".exploration-activity-group .event-title-row")?.textContent)
      .toContain("正在探索代码库");
    expect(stream.querySelectorAll(".read-file-item")).toHaveLength(3);
    expect(stream.textContent).toContain("app");
  });

  it("renders a recoverable exploration failure as a warning", () => {
    const events = [
      event("run_started", { task: "定位发布异常。", model: "test-model", cwd: "D:/demo" }),
      event("tool_call_started", {
        callId: "list-ok",
        name: "list_dir",
        args: { path: "app" },
      }),
      event("tool_call_finished", {
        callId: "list-ok",
        name: "list_dir",
        ok: true,
        summary: "app 共 6 项",
      }),
      event("tool_call_started", {
        callId: "read-failed",
        name: "read_file",
        args: { path: "app/missing.py" },
      }),
      event("tool_call_finished", {
        callId: "read-failed",
        name: "read_file",
        ok: false,
        detail: "文件不存在",
      }),
    ];
    const host = mountTimeline(events, true);
    const group = host.querySelector(".exploration-activity-group");

    expect(group?.getAttribute("data-variant")).toBe("warning");
    expect(group?.querySelector(".event-marker")?.textContent).toBe("!");
    expect(group?.textContent).toContain("1 个失败");
  });

  it("groups a change stage by file instead of by tool type", () => {
    const events = [
      event("run_started", { task: "补充活动冲突提示。", model: "test-model", cwd: "D:/demo" }),
      event("tool_call_started", {
        callId: "edit-1",
        name: "edit_file",
        args: {
          file_path: "app/api/routes.py",
          old_string: "old route",
          new_string: "new route",
        },
      }),
      event("tool_call_finished", {
        callId: "edit-1",
        name: "edit_file",
        ok: true,
        modifiedPaths: ["app/api/routes.py"],
      }),
      event("tool_call_started", {
        callId: "edit-2",
        name: "edit_file",
        args: { path: "app/web/static/app.js", old_text: "old toast", new_text: "new toast" },
      }),
      event("tool_call_finished", {
        callId: "edit-2",
        name: "edit_file",
        ok: true,
        modifiedPaths: ["app/web/static/app.js"],
      }),
      event("tool_call_started", {
        callId: "write-1",
        name: "write_file",
        args: { path: "tests/test_conflict.py", content: "def test_conflict(): pass" },
      }),
      event("tool_call_finished", {
        callId: "write-1",
        name: "write_file",
        ok: true,
        createdPaths: ["tests/test_conflict.py"],
      }),
      event("tool_call_started", {
        callId: "test-1",
        name: "run_command",
        args: { command: "python -m pytest -q" },
      }),
    ];
    const host = mountTimeline(events, true);
    const stream = host.querySelector(".assistant-activity-stream")!;

    expect(stream.querySelectorAll(":scope > .tool-activity")).toHaveLength(4);
    expect(stream.querySelectorAll(".file-change-group")).toHaveLength(3);
    expect(stream.querySelector(".file-change-group .event-title-row")?.textContent)
      .toContain("修改 app/api/routes.py");
    expect(stream.textContent).toContain("app/api/routes.py");
    expect(stream.textContent).toContain("old route");
    expect(stream.textContent).toContain("new route");
    expect(stream.textContent).toContain("tests/test_conflict.py");
  });

  it("collapses read-edit-reread-reedit for one file into one recovered file card", () => {
    const events = [
      event("run_started", { task: "补充 Priority 编辑。", model: "test-model", cwd: "D:/demo" }),
      event("tool_call_started", {
        callId: "read-a",
        name: "read_file",
        args: { path: "app/api/routes.py" },
      }),
      event("tool_call_finished", {
        callId: "read-a",
        name: "read_file",
        ok: true,
        touchedPaths: ["app/api/routes.py"],
      }),
      event("tool_call_started", {
        callId: "edit-a",
        name: "edit_file",
        args: { path: "app/api/routes.py", old_text: "missing", new_text: "first" },
      }),
      event("tool_call_finished", {
        callId: "edit-a",
        name: "edit_file",
        ok: false,
        summary: "定位串没有匹配",
      }),
      event("tool_call_started", {
        callId: "read-b",
        name: "read_file",
        args: { file_path: ".\\app\\api\\routes.py" },
      }),
      event("tool_call_finished", {
        callId: "read-b",
        name: "read_file",
        ok: true,
        touchedPaths: ["app/api/routes.py"],
      }),
      event("tool_call_started", {
        callId: "edit-b",
        name: "edit_file",
        args: { path: "app/api/routes.py", old_text: "old", new_text: "new" },
      }),
      event("tool_call_finished", {
        callId: "edit-b",
        name: "edit_file",
        ok: true,
        modifiedPaths: ["app/api/routes.py"],
      }),
      event("tool_call_started", {
        callId: "read-c",
        name: "read_file",
        args: { path: "app/api/routes.py" },
      }),
      event("tool_call_finished", {
        callId: "read-c",
        name: "read_file",
        ok: true,
        touchedPaths: ["app/api/routes.py"],
      }),
    ];
    const host = mountTimeline(events);
    const cards = host.querySelectorAll(".file-change-group");

    expect(cards).toHaveLength(1);
    expect(cards[0]?.getAttribute("data-variant")).toBe("warning");
    expect(cards[0]?.textContent).toContain("读取 3 次 · 修改 2 次 · 1 次失败 · 已重新检查");
    expect(cards[0]?.textContent).toContain("失败后已恢复");
    expect(cards[0]?.querySelectorAll(".file-change-calls > li")).toHaveLength(5);
  });

  it("starts a new file card when a test command separates two edits", () => {
    const events = [
      event("run_started", { task: "修复并验证。", model: "test-model", cwd: "D:/demo" }),
      event("tool_call_started", {
        callId: "edit-before-test",
        name: "edit_file",
        args: { path: "app/main.py", old_text: "old", new_text: "first" },
      }),
      event("tool_call_finished", {
        callId: "edit-before-test",
        name: "edit_file",
        ok: true,
        modifiedPaths: ["app/main.py"],
      }),
      event("tool_call_started", {
        callId: "test-boundary",
        name: "run_command",
        args: { command: "pytest -q" },
      }),
      event("tool_call_finished", {
        callId: "test-boundary",
        name: "run_command",
        ok: false,
        verificationKind: "test",
      }),
      event("tool_call_started", {
        callId: "edit-after-test",
        name: "edit_file",
        args: { path: "app/main.py", old_text: "first", new_text: "fixed" },
      }),
      event("tool_call_finished", {
        callId: "edit-after-test",
        name: "edit_file",
        ok: true,
        modifiedPaths: ["app/main.py"],
      }),
    ];
    const host = mountTimeline(events);

    expect(host.querySelectorAll(".file-change-group")).toHaveLength(2);
    expect(host.querySelectorAll(".tool-activity")).toHaveLength(3);
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

  it("shows honest live progress without exposing a user-facing step limit", () => {
    const events = [
      event("run_started", { task: "补充 Priority 逻辑。", model: "test-model", cwd: "D:/demo" }),
      event("turn_started", { step: 41, maxSteps: 100 }),
      event("context_stats", { usedTokens: 3200, limit: 1000000, messageCount: 18 }),
    ];
    const host = mountTimeline(events, true);

    expect(host.querySelector(".assistant-pending")?.textContent)
      .toContain("正在思考您的问题…");
    expect(host.querySelector(".progress-dots")).not.toBeNull();
    expect(host.querySelector(".runtime-details summary")?.textContent)
      .toContain("模型决策 41 次");
    expect(host.querySelector(".runtime-details summary")?.textContent)
      .not.toContain("/ 100");
  });

  it("derives specific live progress from the latest real tool evidence", () => {
    const events = [
      event("run_started", { task: "定位发布异常。", model: "test-model", cwd: "D:/demo" }),
      event("tool_call_started", {
        callId: "read-1",
        name: "read_file",
        args: { path: "app/repositories/campaign_repository.py" },
      }),
      event("tool_call_finished", {
        callId: "read-1",
        name: "read_file",
        ok: true,
        touchedPaths: ["app/repositories/campaign_repository.py"],
      }),
      event("turn_started", { step: 4, maxSteps: 100 }),
      event("context_stats", { usedTokens: 7200, limit: 1000000, messageCount: 12 }),
    ];
    const host = mountTimeline(events, true);

    expect(host.querySelector(".assistant-pending")?.textContent)
      .toContain("已读取 repositories/campaign_repository.py，正在结合相关代码定位问题");
  });
});
