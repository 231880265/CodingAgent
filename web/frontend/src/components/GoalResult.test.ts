// @vitest-environment jsdom

import { createApp } from "vue";
import { afterEach, describe, expect, it } from "vitest";
import type { App } from "vue";
import type { HakoEvent, HakoEventType, StopReason } from "../types/api";
import GoalResult from "./GoalResult.vue";

let eventId = 100;
let mountedApp: App<Element> | null = null;

function event(type: HakoEventType, payload: Record<string, unknown>): HakoEvent {
  eventId += 1;
  return {
    schemaVersion: "1.0",
    eventId,
    sessionId: "session-ui",
    runId: "run-ui",
    type,
    source: "WORKER",
    occurredAt: "2026-08-29T09:00:00.000Z",
    payload,
  };
}

function result(
  stopReason: StopReason,
  finalText: string,
  changedPaths: string[] = [],
  verification: Array<{ kind: string; command: string; summary: string; step: number }> = [],
): HakoEvent {
  return event("run_result", {
    success: ["done_read_only", "done_verified"].includes(stopReason),
    stopReason,
    finalText,
    changedPaths,
    verification,
    steps: 4,
    totalTokens: 2400,
  });
}

function mountResult(events: HakoEvent[]): HTMLElement {
  const host = document.createElement("div");
  document.body.appendChild(host);
  mountedApp = createApp(GoalResult, {
    event: events.at(-1)!,
    events,
  });
  mountedApp.mount(host);
  return host;
}

afterEach(() => {
  mountedApp?.unmount();
  mountedApp = null;
  document.body.innerHTML = "";
});

describe("GoalResult presentation", () => {
  it("shows a normal question as an assistant Markdown answer", () => {
    const completed = result(
      "done_read_only",
      "## Java 与 C++\n\n| 维度 | Java | C++ |\n| --- | --- | --- |\n| 内存 | GC | RAII |\n\n```java\nclass Demo {}\n```",
    );
    const host = mountResult([completed]);

    expect(host.querySelector(".assistant-answer")).not.toBeNull();
    expect(host.querySelector("h2")?.textContent).toBe("Java 与 C++");
    expect(host.querySelector("table")).not.toBeNull();
    expect(host.querySelector("pre code.language-java")).not.toBeNull();
    expect(host.querySelector(".verified-result")).toBeNull();
    expect(host.textContent).not.toContain("调查已完成");
    expect(host.textContent).not.toContain("查看完整交付证据");
  });

  it("shows read-only repository work as a lightweight analysis result", () => {
    const started = event("tool_call_started", {
      callId: "read-a",
      name: "read_file",
      args: { path: "scheduler.py" },
    });
    const finished = event("tool_call_finished", {
      callId: "read-a",
      name: "read_file",
      ok: true,
      summary: "已读取 scheduler.py",
      touchedPaths: ["scheduler.py"],
    });
    const completed = result("done_read_only", "锁顺序相反导致死锁。");
    const host = mountResult([started, finished, completed]);

    expect(host.querySelector(".analysis-result")).not.toBeNull();
    expect(host.textContent).toContain("scheduler.py");
    expect(host.textContent).toContain("查看调查过程");
    expect(host.querySelector(".verified-result")).toBeNull();
  });

  it("reserves the green verified result for evidenced code changes", () => {
    const completed = result(
      "done_verified",
      "修复了结算边界条件。",
      ["checkout.py", "pricing.py"],
      [{ kind: "test", command: "pytest -q", summary: "12 passed", step: 4 }],
    );
    const host = mountResult([completed]);

    expect(host.querySelector(".verified-result")).not.toBeNull();
    expect(host.textContent).toContain("修改已验证");
    expect(host.textContent).toContain("pytest -q");
  });

  it.each([
    ["done_unverified", "修改尚未验证"],
    ["error", "运行失败"],
    ["cancelled", "本轮已取消"],
  ] as const)("renders %s as a non-success outcome", (reason, title) => {
    const completed = result(reason, "没有形成可信成功。", ["partial.py"]);
    const host = mountResult([completed]);

    expect(host.querySelector(".outcome-result")).not.toBeNull();
    expect(host.querySelector(".verified-result")).toBeNull();
    expect(host.textContent).toContain(title);
  });

  it("shows a safety-budget stop as resumable instead of a run failure", () => {
    const failedStatus = event("run_status", { current: "FAILED" });
    const completed = result("max_steps", "已完成部分修改。", ["partial.py"]);
    const host = mountResult([failedStatus, completed]);

    expect(host.textContent).toContain("本轮已暂停，可以继续");
    expect(host.textContent).toContain("Conversation 和已落盘修改均已保留");
    expect(host.textContent).not.toContain("运行失败");
  });
});
