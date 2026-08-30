<script setup lang="ts">
import type { SessionHistoryItem } from "../types/api";

defineProps<{
  open: boolean;
  items: SessionHistoryItem[];
  busy: boolean;
}>();

const emit = defineEmits<{
  close: [];
  select: [sessionId: string];
}>();

function workspaceName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? path;
}

function timestamp(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
</script>

<template>
  <div v-if="open" class="history-overlay" @click.self="emit('close')">
    <aside class="history-drawer" aria-label="历史 Session">
      <header>
        <div>
          <span class="eyebrow">SESSION HISTORY</span>
          <h2>历史会话</h2>
        </div>
        <button type="button" aria-label="关闭历史" @click="emit('close')">×</button>
      </header>
      <p class="history-explainer">历史只读可查；已关闭 Worker 的 Conversation 不会伪恢复。</p>
      <div v-if="items.length" class="history-list">
        <button
          v-for="item in items"
          :key="item.sessionId"
          type="button"
          class="history-item"
          :disabled="busy"
          @click="emit('select', item.sessionId)"
        >
          <span class="history-item-title">{{ item.lastPrompt || "未命名工程会话" }}</span>
          <span class="history-item-meta">
            {{ workspaceName(item.workspace) }} · {{ item.runCount }} Run · {{ timestamp(item.createdAt) }}
          </span>
          <span class="history-item-status" :data-status="item.status">{{ item.status }}</span>
        </button>
      </div>
      <div v-else class="history-empty">还没有持久化的 Session。</div>
    </aside>
  </div>
</template>
