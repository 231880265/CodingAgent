<script setup lang="ts">
import { computed } from "vue";
import type { RunPresentation } from "../utils/runPresentation";
import { STOP_REASON_LABELS, formatTokens } from "../utils/presentation";

const props = defineProps<{ view: RunPresentation }>();
const reasonLabel = computed(() => props.view.stopReason
  ? STOP_REASON_LABELS[props.view.stopReason]
  : props.view.terminalStatus ?? "未知");
</script>

<template>
  <details class="run-diagnostics">
    <summary>运行详情</summary>
    <dl>
      <div>
        <dt>终止原因</dt>
        <dd>{{ reasonLabel }}</dd>
      </div>
      <div>
        <dt>模型决策</dt>
        <dd>{{ view.steps }} 次</dd>
      </div>
      <div>
        <dt>上下文</dt>
        <dd>{{ formatTokens(view.totalTokens) }} tokens</dd>
      </div>
      <div v-if="view.toolActivities.length">
        <dt>工具调用</dt>
        <dd>{{ view.toolActivities.length }} 次</dd>
      </div>
    </dl>
  </details>
</template>
