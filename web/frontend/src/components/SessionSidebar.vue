<script setup lang="ts">
import type { SessionHistoryItem } from "../types/api";

defineProps<{
  open: boolean;
  items: SessionHistoryItem[];
  busy: boolean;
  activeSessionId: string | null;
}>();

const emit = defineEmits<{
  close: [];
  select: [sessionId: string];
  delete: [sessionId: string];
  newSession: [];
}>();
</script>

<template>
  <button
    v-if="open"
    type="button"
    class="session-sidebar-scrim"
    aria-label="关闭会话列表"
    @click="emit('close')"
  ></button>
  <aside class="session-sidebar" :class="{ 'is-open': open }" aria-label="会话列表">
    <div class="session-sidebar-heading">
      <strong>会话</strong>
      <button
        type="button"
        class="session-sidebar-close"
        aria-label="关闭会话列表"
        @click="emit('close')"
      >×</button>
    </div>

    <button
      type="button"
      class="sidebar-new-session"
      :disabled="busy"
      @click="emit('newSession')"
    >
      <span aria-hidden="true">＋</span>
      新会话
    </button>

    <nav v-if="items.length" class="session-list" aria-label="历史会话">
      <div
        v-for="item in items"
        :key="item.sessionId"
        class="session-list-row"
        :class="{ 'is-current': item.sessionId === activeSessionId }"
      >
        <button
          type="button"
          class="session-list-item"
          :aria-current="item.sessionId === activeSessionId ? 'page' : undefined"
          :disabled="busy"
          :title="item.lastPrompt || '未命名工程会话'"
          @click="emit('select', item.sessionId)"
        >
          <span class="session-list-title">{{ item.lastPrompt || "未命名工程会话" }}</span>
        </button>
        <button
          type="button"
          class="session-delete-button"
          :disabled="busy"
          :aria-label="`删除会话：${item.lastPrompt || '未命名工程会话'}`"
          title="删除会话"
          @click.stop="emit('delete', item.sessionId)"
        >×</button>
      </div>
    </nav>
    <p v-else class="session-list-empty">完成第一轮任务后，会话会出现在这里。</p>
  </aside>
</template>
