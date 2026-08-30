<script setup lang="ts">
import { computed } from "vue";
import { Tag } from "vant";
import type { RunResource, RunSummary } from "../types/api";
import {
  STOP_REASON_LABELS,
  formatTokens,
  statusPresentation,
} from "../utils/presentation";

const props = defineProps<{ task: RunResource | null; summary: RunSummary | null }>();
const presentation = computed(() =>
  statusPresentation(
    props.summary?.status ?? props.task?.status ?? "IDLE",
    props.summary?.stopReason ?? null,
  ),
);
const tagType = computed(() => {
  if (presentation.value.tone === "success") return "success";
  if (presentation.value.tone === "warning") return "warning";
  if (presentation.value.tone === "danger") return "danger";
  return "default";
});
const stopReasonLabel = computed(() => {
  if (!props.summary?.stopReason) return "无 Agent 停止原因";
  return STOP_REASON_LABELS[props.summary.stopReason];
});
const resultTitle = computed(() => {
  if (!props.summary) return "等待最终证据";
  if (props.summary.status === "CANCELLED") return "任务已取消";
  if (props.summary.success) return "完成条件已满足";
  if (props.summary.stopReason === "done_unverified") return "任务已结束，验证证据不足";
  if (props.summary.stopReason === "denied") return "任务因审批拒绝而停止";
  return "任务未满足完成条件";
});
const resultSymbol = computed(() => {
  if (props.summary?.success) return "✓";
  if (presentation.value.tone === "warning") return "!";
  if (props.summary?.status === "CANCELLED") return "■";
  return "×";
});
</script>

<template>
  <section class="evidence-panel" aria-labelledby="evidence-title">
    <div class="section-heading evidence-heading">
      <div>
        <p class="eyebrow">EVIDENCE</p>
        <h2 id="evidence-title">完成证据</h2>
      </div>
      <Tag v-if="summary" :type="tagType" plain>{{ presentation.label }}</Tag>
    </div>

    <div v-if="!task" class="evidence-empty">
      <p>任务结束后，这里只展示内核能够证明的结果。</p>
      <ul><li>停止原因</li><li>实际变更路径</li><li>最终版本验证</li></ul>
    </div>

    <template v-else-if="!summary">
      <div class="pending-evidence">
        <span class="pending-symbol" aria-hidden="true">…</span>
        <div><strong>{{ resultTitle }}</strong><p>模型说明不会提前计作成功，等待 RunResult。</p></div>
      </div>
      <dl class="evidence-facts">
        <div><dt>当前状态</dt><dd>{{ presentation.label }}</dd></div>
        <div><dt>模型决策</dt><dd>{{ task.progress.step ?? 0 }} / {{ task.progress.maxSteps }}</dd></div>
        <div><dt>上下文</dt><dd>{{ formatTokens(task.progress.usedTokens) }} tokens</dd></div>
      </dl>
    </template>

    <template v-else>
      <div class="result-line" :data-tone="presentation.tone">
        <span class="result-symbol" aria-hidden="true">{{ resultSymbol }}</span>
        <div><strong>{{ resultTitle }}</strong><p>{{ stopReasonLabel }}</p></div>
      </div>

      <dl class="evidence-facts">
        <div><dt>模型决策</dt><dd>{{ summary.steps }}</dd></div>
        <div><dt>总消耗</dt><dd>{{ formatTokens(summary.totalTokens) }} tokens</dd></div>
      </dl>

      <div class="evidence-section">
        <h3>变更文件</h3>
        <ul v-if="summary.changedPaths.length" class="path-list">
          <li v-for="path in summary.changedPaths" :key="path"><code>{{ path }}</code></li>
        </ul>
        <p v-else class="muted-copy">没有记录到文件变化。</p>
      </div>

      <div class="evidence-section">
        <h3>最终版本验证</h3>
        <div v-for="evidence in summary.verification" :key="`${evidence.step}-${evidence.command}`" class="verification-entry">
          <div class="verification-result"><span aria-hidden="true">✓</span><strong>{{ evidence.summary }}</strong></div>
          <code>{{ evidence.command }}</code>
          <small>第 {{ evidence.step }} 次模型决策后有效</small>
        </div>
        <p v-if="!summary.verification.length" class="muted-copy">
          {{ summary.stopReason === "done_read_only" ? "只读任务没有修改后验证要求。" : "最后一次修改后没有受认可的成功验证。" }}
        </p>
      </div>

      <details v-if="summary.finalText" class="evidence-section final-note">
        <summary>查看 Agent 交付说明</summary>
        <p>{{ summary.finalText }}</p>
      </details>

      <p class="evidence-disclaimer">Verified Finish 只证明最后一次修改后存在成功验证，不代表测试覆盖全部风险。</p>
    </template>
  </section>
</template>
