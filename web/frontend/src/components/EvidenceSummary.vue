<script setup lang="ts">
import { computed } from "vue";
import { Tag } from "vant";
import type { TaskResource, TaskSummary } from "../types/api";
import {
  STATUS_LABELS,
  STOP_REASON_LABELS,
  formatTokens,
} from "../utils/presentation";

const props = defineProps<{
  task: TaskResource | null;
  summary: TaskSummary | null;
}>();

const stopReasonLabel = computed(() => {
  if (!props.summary?.stopReason) return "无 Agent 停止原因";
  return STOP_REASON_LABELS[props.summary.stopReason];
});

const resultTitle = computed(() => {
  if (!props.summary) return "等待最终证据";
  if (props.summary.status === "CANCELLED") return "任务已取消";
  return props.summary.success ? "完成条件已满足" : "完成条件未满足";
});
</script>

<template>
  <section class="evidence-panel" aria-labelledby="evidence-title">
    <div class="section-heading evidence-heading">
      <div>
        <p class="eyebrow">EVIDENCE</p>
        <h2 id="evidence-title">完成证据</h2>
      </div>
      <Tag v-if="summary" :type="summary.success ? 'success' : 'danger'" plain>
        {{ STATUS_LABELS[summary.status] }}
      </Tag>
    </div>

    <div v-if="!task" class="evidence-empty">
      <p>任务结束后，这里只展示内核能够证明的结果。</p>
      <ul>
        <li>停止原因</li>
        <li>实际变更路径</li>
        <li>最终版本验证</li>
      </ul>
    </div>

    <template v-else-if="!summary">
      <div class="pending-evidence">
        <span class="pending-symbol" aria-hidden="true">…</span>
        <div>
          <strong>{{ resultTitle }}</strong>
          <p>模型的说明不会提前计作成功，等待 RunResult。</p>
        </div>
      </div>
      <dl class="evidence-facts">
        <div>
          <dt>当前状态</dt>
          <dd>{{ STATUS_LABELS[task.status] }}</dd>
        </div>
        <div>
          <dt>模型回合</dt>
          <dd>{{ task.progress.step ?? 0 }} / {{ task.progress.maxSteps }}</dd>
        </div>
        <div>
          <dt>上下文</dt>
          <dd>{{ formatTokens(task.progress.usedTokens) }} tokens</dd>
        </div>
      </dl>
    </template>

    <template v-else>
      <div class="result-line" :data-success="summary.success">
        <span class="result-symbol" aria-hidden="true">
          {{ summary.success ? "✓" : summary.status === "CANCELLED" ? "■" : "×" }}
        </span>
        <div>
          <strong>{{ resultTitle }}</strong>
          <p>{{ stopReasonLabel }}</p>
        </div>
      </div>

      <dl class="evidence-facts">
        <div>
          <dt>回合</dt>
          <dd>{{ summary.steps }}</dd>
        </div>
        <div>
          <dt>总消耗</dt>
          <dd>{{ formatTokens(summary.totalTokens) }} tokens</dd>
        </div>
      </dl>

      <div class="evidence-section">
        <h3>变更文件</h3>
        <ul v-if="summary.changedPaths.length" class="path-list">
          <li v-for="path in summary.changedPaths" :key="path">
            <code>{{ path }}</code>
          </li>
        </ul>
        <p v-else class="muted-copy">没有记录到文件变化。</p>
      </div>

      <div class="evidence-section">
        <h3>最终版本验证</h3>
        <div
          v-for="evidence in summary.verification"
          :key="`${evidence.step}-${evidence.command}`"
          class="verification-entry"
        >
          <div class="verification-result">
            <span aria-hidden="true">✓</span>
            <strong>{{ evidence.summary }}</strong>
          </div>
          <code>{{ evidence.command }}</code>
          <small>第 {{ evidence.step }} 轮之后有效</small>
        </div>
        <p v-if="!summary.verification.length" class="muted-copy">
          {{
            summary.stopReason === "done_read_only"
              ? "只读任务没有修改后验证要求。"
              : "没有可用的修改后验证证据。"
          }}
        </p>
      </div>

      <div v-if="summary.finalText" class="evidence-section final-note">
        <h3>Agent 交付说明</h3>
        <p>{{ summary.finalText }}</p>
      </div>

      <p class="evidence-disclaimer">
        Verified Finish 证明最后一次修改后存在成功验证，不代表测试覆盖全部风险。
      </p>
    </template>
  </section>
</template>
