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

describe("AppHeader hierarchy", () => {
  it("uses a text brand and leaves model selection to the composer", () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    mountedApp = createApp(AppHeader, {
      connection: "UP",
      streamConnected: true,
      runStatus: "RUNNING",
      sessionStatus: "OPEN",
      mode: "api",
      workspace: "D:\\demo\\repository",
      stopReason: null,
    });
    mountedApp.mount(host);

    expect(host.querySelector(".brand-mark")).toBeNull();
    expect(host.querySelector(".brand-name")?.textContent).toBe("hako");
    expect(host.querySelector(".brand-caret")).not.toBeNull();
    expect(host.querySelector(".model-name")).toBeNull();
  });
});
