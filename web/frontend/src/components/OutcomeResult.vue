<script setup lang="ts">
import { computed } from "vue";
import type { HakoEvent } from "../types/api";
import type { RunPresentation } from "../utils/runPresentation";
import MarkdownContent from "./MarkdownContent.vue";
import RunDiagnostics from "./RunDiagnostics.vue";

const props = defineProps<{ event: HakoEvent; view: RunPresentation }>();
const tone = computed(() => props.view.kind === "error" ? "danger" : "warning");
const title = computed(() => ({
  unverified: "修改尚未验证",
  cancelled: "本轮已取消",
  denied: "操作已拒绝",
  error: "运行失败",
  incomplete: "本轮未完成",
  conversation: "本轮已完成",
  analysis: "分析完成",
  verified_change: "修改已验证",
})[props.view.kind]);
</script>

<template>
  <section class="outcome-result" :data-tone="tone">
    <div class="outcome-mark" aria-hidden="true">{{ tone === "danger" ? "×" : "!" }}</div>
    <div>
      <header class="result-heading">
        <h3>{{ title }}</h3>
      </header>
      <MarkdownContent v-if="view.finalText" :content="view.finalText" />
      <section v-if="view.changedPaths.length" class="retained-changes">
        <strong>已落盘的修改</strong>
        <code v-for="path in view.changedPaths" :key="path">{{ path }}</code>
      </section>
      <RunDiagnostics :view="view" />
    </div>
  </section>
</template>
