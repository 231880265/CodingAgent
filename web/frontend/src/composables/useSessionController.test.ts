// @vitest-environment jsdom

import { createApp } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSessionController } from "./useSessionController";

describe("useSessionController event batching", () => {
  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
  });

  it("receives SSE immediately but commits queued events to Vue in one 75ms batch", async () => {
    vi.useFakeTimers();
    let controller: ReturnType<typeof useSessionController> | null = null;
    const host = document.createElement("div");
    document.body.appendChild(host);
    const app = createApp({
      setup() {
        controller = useSessionController();
        return () => null;
      },
    });
    app.mount(host);

    await controller!.startSession({
      workspace: "D:/demo",
      prompt: "检查仓库",
      attachments: [],
      options: { maxSteps: 10 },
    });
    await Promise.resolve();

    expect(controller!.events.value).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(74);
    expect(controller!.events.value).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(controller!.events.value).toHaveLength(2);

    app.unmount();
  });
});
