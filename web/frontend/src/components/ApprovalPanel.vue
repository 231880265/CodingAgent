<script setup lang="ts">
import { computed } from "vue";
import { Button, Tag } from "vant";
import type { Approval, ApprovalDecision } from "../types/api";
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
  return "查看完整参数";
});
const oldText = computed(() => props.approval.tool.args.old_text);
const newText = computed(() => props.approval.tool.args.new_text);
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
      <Tag :type="approval.riskLevel === 'HIGH' ? 'danger' : 'warning'" plain>
        {{ approval.riskLevel === "HIGH" ? "高风险" : "有副作用" }}
      </Tag>
    </div>

    <div class="approval-subject">
      <strong>{{ toolLabel }}</strong>
      <code>{{ primarySubject }}</code>
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
      批准后操作才会进入 hako 工具边界；拒绝会以 DENIED 结束任务。
    </p>

    <div class="approval-actions">
      <Button
        class="deny-button"
        :disabled="busy"
        @click="emit('resolve', 'DENY')"
      >
        拒绝
      </Button>
      <Button
        v-if="approval.allowedDecisions.includes('ALLOW_SESSION')"
        plain
        type="primary"
        :disabled="busy"
        @click="emit('resolve', 'ALLOW_SESSION')"
      >
        本任务同类允许
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
