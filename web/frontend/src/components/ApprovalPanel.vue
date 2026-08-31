<script setup lang="ts">
import { computed } from "vue";
import { Button } from "vant";
import type { Approval, ApprovalDecision } from "../types/api";
import { describeApprovalPurpose } from "../utils/approvalPresentation";
import { TOOL_LABELS } from "../utils/presentation";

const props = defineProps<{
  approval: Approval;
  busy: boolean;
}>();

const emit = defineEmits<{
  resolve: [decision: ApprovalDecision];
}>();

const toolLabel = computed(
  () => TOOL_LABELS[props.approval.tool.name] ?? props.approval.tool.name,
);
const primarySubject = computed(() => {
  const args = props.approval.tool.args;
  if (typeof args.command === "string") return args.command;
  if (typeof args.path === "string") return args.path;
  if (typeof args.file_path === "string") return args.file_path;
  return "查看完整参数";
});
const oldText = computed(() => props.approval.tool.args.old_text ?? props.approval.tool.args.old_string);
const newText = computed(() => props.approval.tool.args.new_text ?? props.approval.tool.args.new_string);
const purpose = computed(() => describeApprovalPurpose(
  props.approval.tool.name,
  props.approval.tool.args,
));
</script>

<template>
  <section
    class="approval-panel"
    :data-risk="approval.riskLevel"
    aria-labelledby="approval-title"
    aria-live="assertive"
  >
    <div class="approval-heading">
      <div>
        <p class="eyebrow">APPROVAL</p>
        <h2 id="approval-title">需要你的批准</h2>
      </div>
      <span class="risk-label" :data-risk="approval.riskLevel">
        {{ approval.riskLevel === "HIGH" ? "高风险操作" : "需要确认" }}
      </span>
    </div>

    <div class="approval-subject">
      <strong>{{ toolLabel }}</strong>
      <code>{{ primarySubject }}</code>
    </div>

    <div class="approval-purpose">
      <strong>为什么需要这一步</strong>
      <p>{{ purpose }}</p>
    </div>

    <p v-if="approval.dangerReason" class="danger-reason">
      {{ approval.dangerReason }}
    </p>

    <details v-if="oldText !== undefined || newText !== undefined" class="change-preview">
      <summary>查看拟议修改</summary>
      <div v-if="oldText !== undefined" class="code-block removed-code">
        <span>原片段</span>
        <pre>{{ oldText }}</pre>
      </div>
      <div v-if="newText !== undefined" class="code-block added-code">
        <span>新片段</span>
        <pre>{{ newText }}</pre>
      </div>
    </details>

    <p class="approval-note">
      操作尚未执行。拒绝只否决这一次调用，Agent 可改用更安全的方案继续。
    </p>

    <div class="approval-actions">
      <Button
        class="deny-button"
        :disabled="busy"
        @click="emit('resolve', 'DENY')"
      >
        拒绝并调整
      </Button>
      <Button
        v-if="approval.allowedDecisions.includes('ALLOW_SESSION')"
        plain
        class="session-allow-button"
        :disabled="busy"
        @click="emit('resolve', 'ALLOW_SESSION')"
      >
        本会话允许同类操作
      </Button>
      <Button
        type="primary"
        :loading="busy"
        loading-text="正在提交"
        @click="emit('resolve', 'ALLOW_ONCE')"
      >
        允许这一次
      </Button>
    </div>
  </section>
</template>
