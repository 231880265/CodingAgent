<script setup lang="ts">
import { computed } from "vue";
import type { HakoEvent } from "../types/api";
import type { RunPresentation } from "../utils/runPresentation";
import MarkdownContent from "./MarkdownContent.vue";
import RunDiagnostics from "./RunDiagnostics.vue";

const props = defineProps<{ event: HakoEvent; view: RunPresentation }>();
const tone = computed(() => props.view.kind === "error" ? "danger" : "warning");
const title = computed(() => {
  if (props.view.kind === "incomplete" && props.view.stopReason === "max_steps") {
    return "本轮已暂停，可以继续";
  }
  return ({
    unverified: "修改尚未验证",
    cancelled: "本轮已取消",
    denied: "操作已拒绝",
    error: "运行失败",
    incomplete: "本轮未完成",
    conversation: "本轮已完成",
    analysis: "分析完成",
    verified_change: "修改已验证",
  })[props.view.kind];
});
const guidance = computed(() => {
  if (props.view.stopReason === "max_steps") {
    return "本轮触发了内部失控保险；Conversation 和已落盘修改均已保留，可在下方继续让 hako 完成验证。";
  }
  if (props.view.stopReason === "stuck") {
    return "内核检测到无进展的重复调用并主动止损；可以补充信息或换一个方向继续。";
  }
  return "";
});
</script>

<template>
  <section class="outcome-result" :data-tone="tone">
    <div class="outcome-mark" aria-hidden="true">{{ tone === "danger" ? "×" : "!" }}</div>
    <div>
      <header class="result-heading">
        <h3>{{ title }}</h3>
      </header>
      <p v-if="guidance" class="outcome-guidance">{{ guidance }}</p>
      <MarkdownContent v-if="view.finalText" :content="view.finalText" />
      <section v-if="view.changedPaths.length" class="retained-changes">
        <strong>已落盘的修改</strong>
        <code v-for="path in view.changedPaths" :key="path">{{ path }}</code>
      </section>
      <RunDiagnostics :view="view" />
    </div>
  </section>
</template>
