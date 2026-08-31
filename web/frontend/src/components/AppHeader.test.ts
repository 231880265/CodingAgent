// @vitest-environment jsdom

import { createApp } from "vue";
import { afterEach, describe, expect, it } from "vitest";
import type { App } from "vue";
import AppHeader from "./AppHeader.vue";

let mountedApp: App<Element> | null = null;

afterEach(() => {
  mountedApp?.unmount();
  mountedApp = null;
  document.body.innerHTML = "";
});

describe("AppHeader model source", () => {
  it("shows the model reported by the current Worker without provider hardcoding", () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    mountedApp = createApp(AppHeader, {
      connection: "UP",
      streamConnected: true,
      runStatus: "RUNNING",
      sessionStatus: "OPEN",
      mode: "api",
      model: "gpt-5.2",
      workspace: "D:\\demo\\repository",
      stopReason: null,
    });
    mountedApp.mount(host);

    const model = host.querySelector<HTMLElement>(".model-name");
    expect(model?.textContent).toBe("gpt-5.2");
    expect(model?.title).toBe("gpt-5.2");
    expect(host.textContent).not.toContain("DeepSeek-V4-Flash");
  });
});
