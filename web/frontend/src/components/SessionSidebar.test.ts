// @vitest-environment jsdom

import { createApp } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";
import SessionSidebar from "./SessionSidebar.vue";

let mountedApp: App<Element> | null = null;

afterEach(() => {
  mountedApp?.unmount();
  mountedApp = null;
  document.body.innerHTML = "";
});

describe("SessionSidebar", () => {
  it("shows resumable conversations without exposing lifecycle labels", async () => {
    const selected = vi.fn();
    const deleted = vi.fn();
    const host = document.createElement("div");
    document.body.appendChild(host);
    mountedApp = createApp(SessionSidebar, {
      open: true,
      busy: false,
      activeSessionId: "session-1",
      items: [
        {
          sessionId: "session-1",
          workspace: "D:\\demo\\promoops",
          status: "SUSPENDED",
          runCount: 2,
          createdAt: "2026-08-30T08:00:00.000Z",
          closedAt: null,
          lastPrompt: "修复发布后线上仍读旧版本",
        },
      ],
      onSelect: selected,
      onDelete: deleted,
    });
    mountedApp.mount(host);

    expect(host.textContent).toContain("修复发布后线上仍读旧版本");
    expect(host.textContent).not.toContain("promoops · 2 轮");
    expect(host.textContent).not.toContain("08/30");
    expect(host.textContent).not.toContain("SUSPENDED");
    expect(host.textContent).not.toContain("OPEN");
    expect(host.textContent).not.toContain("CLOSED");
    expect(host.textContent).not.toContain("历史只读可查");
    expect(host.textContent).not.toContain("已关闭 Worker");

    (host.querySelector(".session-list-item") as HTMLButtonElement).click();
    await Promise.resolve();
    expect(selected).toHaveBeenCalledWith("session-1");

    (host.querySelector(".session-delete-button") as HTMLButtonElement).click();
    await Promise.resolve();
    expect(deleted).toHaveBeenCalledWith("session-1");
  });
});
