<script setup lang="ts">
import { computed } from "vue";
import { Tag } from "vant";
import type { TaskStatus } from "../types/api";
import { STATUS_LABELS } from "../utils/presentation";

const props = defineProps<{
  connection: "CHECKING" | "UP" | "DOWN";
  streamConnected: boolean;
  status: TaskStatus | "IDLE";
  mode: "mock" | "api";
  model: string | null;
}>();

const connectionLabel = computed(() => {
  if (props.connection === "CHECKING") return "正在检查服务";
  if (props.connection === "DOWN") return "服务未连接";
  if (props.status !== "IDLE" && props.streamConnected) return "事件流已连接";
  return "服务可用";
});
</script>

<template>
  <header class="app-header">
    <div class="brand-block">
      <div class="brand-mark" aria-hidden="true">h</div>
      <div>
        <div class="brand-name">hako</div>
        <div class="brand-caption">工程任务控制台</div>
      </div>
    </div>

    <div class="header-context" aria-label="当前运行信息">
      <span v-if="model" class="model-name" :title="model">{{ model }}</span>
      <Tag plain class="mode-tag">
        {{ mode === "mock" ? "界面演示" : "本地 API" }}
      </Tag>
      <span class="connection-state" :data-state="connection">
        <span class="status-dot" aria-hidden="true"></span>
        {{ connectionLabel }}
      </span>
      <span class="run-state" :data-status="status">
        {{ STATUS_LABELS[status] }}
      </span>
    </div>
  </header>
</template>
