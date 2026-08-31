<script setup lang="ts">
import { computed } from "vue";
import type { RunStatus, SessionStatus, StopReason } from "../types/api";
import { statusPresentation } from "../utils/presentation";

const props = defineProps<{
  connection: "CHECKING" | "UP" | "DOWN";
  streamConnected: boolean;
  runStatus: RunStatus | "IDLE";
  sessionStatus: SessionStatus | "NONE";
  mode: "mock" | "api";
  model: string | null;
  workspace: string | null;
  stopReason: StopReason | null;
}>();

const emit = defineEmits<{
  history: [];
}>();
const runPresentation = computed(() =>
  statusPresentation(props.runStatus, props.stopReason),
);
const workspaceName = computed(() => {
  if (!props.workspace) return "";
  const parts = props.workspace.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? props.workspace;
});
const connectionLabel = computed(() => {
  if (props.connection === "CHECKING") return "检查服务";
  if (props.connection === "DOWN") return "服务断开";
  if (props.streamConnected) return "在线";
  if (props.sessionStatus === "CLOSED") return "会话已关闭";
  return "服务可用";
});
</script>

<template>
  <header class="app-header">
    <div class="brand-block">
      <div class="brand-mark" aria-hidden="true">h</div>
      <strong class="brand-name">hako</strong>
    </div>

    <div
      v-if="workspace"
      class="header-workspace"
      :title="workspace"
    >
      <span class="folder-mark" aria-hidden="true"></span>
      <span>{{ workspaceName }}</span>
    </div>

    <div class="header-context" aria-label="当前会话状态">
      <button type="button" class="header-quiet-action" @click="emit('history')">
        会话
      </button>
      <span v-if="model" class="model-name" :title="model">{{ model }}</span>
      <span class="connection-state" :data-state="connection">
        <span class="status-dot" aria-hidden="true"></span>
        {{ mode === "mock" ? "界面演示" : connectionLabel }}
      </span>
      <span v-if="runStatus !== 'IDLE'" class="run-state" :data-tone="runPresentation.tone">
        {{ runPresentation.label }}
      </span>
    </div>
  </header>
</template>
