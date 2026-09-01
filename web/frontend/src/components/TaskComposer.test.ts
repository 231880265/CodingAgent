// @vitest-environment jsdom

import { createApp, nextTick } from "vue";
import { afterEach, describe, expect, it } from "vitest";
import type { App } from "vue";
import type { CreateSessionRequest } from "../types/api";
import TaskComposer from "./TaskComposer.vue";

let mountedApp: App<Element> | null = null;

afterEach(() => {
  mountedApp?.unmount();
  mountedApp = null;
  document.body.innerHTML = "";
});

describe("TaskComposer Web run policy", () => {
  it("offers a local model display selector without changing the run request", async () => {
    const requests: CreateSessionRequest[] = [];
    const host = document.createElement("div");
    document.body.appendChild(host);
    mountedApp = createApp(TaskComposer, {
      active: false,
      busy: false,
      disabled: false,
      mode: "api",
      model: "claude-sonnet-4-6",
      session: null,
      onStart: (request: CreateSessionRequest) => requests.push(request),
    });
    mountedApp.mount(host);

    const selector = host.querySelector<HTMLSelectElement>(".composer-model-picker select")!;
    expect(selector.value).toBe("claude-sonnet-4-6");
    expect(selector.selectedOptions[0]?.textContent?.trim()).toBe("Claude Sonnet 4.6");
    selector.value = "gpt-5.5";
    selector.dispatchEvent(new Event("change", { bubbles: true }));

    const prompt = host.querySelector<HTMLTextAreaElement>("#goal-prompt")!;
    prompt.value = "解释这段代码";
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();
    host.querySelector("form")!.dispatchEvent(new Event("submit", {
      bubbles: true,
      cancelable: true,
    }));

    expect(requests).toHaveLength(1);
    expect(requests[0]).not.toHaveProperty("model");
  });

  it("allows a knowledge question without choosing a workspace", async () => {
    const requests: CreateSessionRequest[] = [];
    const host = document.createElement("div");
    document.body.appendChild(host);
    mountedApp = createApp(TaskComposer, {
      active: false,
      busy: false,
      disabled: false,
      mode: "api",
      session: null,
      onStart: (request: CreateSessionRequest) => requests.push(request),
    });
    mountedApp.mount(host);

    const prompt = host.querySelector<HTMLTextAreaElement>("#goal-prompt")!;
    prompt.value = "Java 的封装、继承和多态是什么？";
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();

    host.querySelector("form")!.dispatchEvent(new Event("submit", {
      bubbles: true,
      cancelable: true,
    }));

    expect(requests).toHaveLength(1);
    expect(requests[0]!.workspace).toBe("");
  });

  it("hides step configuration and sends the internal Web safety budget", async () => {
    const requests: CreateSessionRequest[] = [];
    const host = document.createElement("div");
    document.body.appendChild(host);
    mountedApp = createApp(TaskComposer, {
      active: false,
      busy: false,
      disabled: false,
      mode: "api",
      session: null,
      onStart: (request: CreateSessionRequest) => requests.push(request),
    });
    mountedApp.mount(host);

    const workspace = host.querySelector<HTMLInputElement>("#workspace-path")!;
    workspace.value = "D:\\demo\\repository";
    workspace.dispatchEvent(new Event("input", { bubbles: true }));
    const prompt = host.querySelector<HTMLTextAreaElement>("#goal-prompt")!;
    prompt.value = "修复失败测试并验证";
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();

    host.querySelector("form")!.dispatchEvent(new Event("submit", {
      bubbles: true,
      cancelable: true,
    }));

    expect(host.querySelector(".composer-settings")).toBeNull();
    expect(requests).toHaveLength(1);
    expect(requests[0]!.options.maxSteps).toBe(100);
  });
});
