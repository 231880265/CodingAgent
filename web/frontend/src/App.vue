<script setup lang="ts">
import { onMounted } from "vue";
import { showConfirmDialog } from "vant";
import AppHeader from "./components/AppHeader.vue";
import ApprovalPanel from "./components/ApprovalPanel.vue";
import RunTimeline from "./components/RunTimeline.vue";
import SessionHistoryDrawer from "./components/SessionHistoryDrawer.vue";
import TaskComposer from "./components/TaskComposer.vue";
import { useSessionController } from "./composables/useSessionController";
import type { CreateRunRequest, CreateSessionRequest } from "./types/api";

const {
  gatewayMode,
  session,
  displayedEvents,
  summary,
  model,
  connection,
  streamConnected,
  runStatus,
  sessionStatus,
  isActive,
  pendingApproval,
  actionPending,
  approvalPending,
  errorMessage,
  historyItems,
  historyOpen,
  selectedHistory,
  viewingHistory,
  initialize,
  startSession,
  createRun,
  resolveApproval,
  cancelRun,
  newSession,
  toggleHistory,
  openHistory,
  closeHistoryView,
  dismissError,
} = useSessionController();

async function submitSession(request: CreateSessionRequest): Promise<void> {
  await startSession(request);
}

async function submitRun(request: CreateRunRequest): Promise<void> {
  await createRun(request);
}

async function confirmCancel(): Promise<void> {
  try {
    await showConfirmDialog({
      title: "停止当前 Run？",
      message: "只停止本轮后续行为；Session、Conversation 和已经落盘的文件修改都会保留。",
      confirmButtonText: "停止本轮",
      cancelButtonText: "继续运行",
      confirmButtonColor: "oklch(52% 0.17 28)",
    });
    await cancelRun();
  } catch {
    // 用户关闭确认框，当前 Run 继续运行。
  }
}

async function confirmNewSession(): Promise<void> {
  if (!session.value) {
    await newSession();
    return;
  }
  try {
    await showConfirmDialog({
      title: "新建独立会话？",
      message: isActive.value
        ? "hako 会先取消当前 Run，再等待 Worker 退出。文件修改不回滚，新 Session 的 Conversation 为空。"
        : "当前 Worker 与 Conversation 会关闭；文件修改保留，新 Session 的上下文为空。",
      confirmButtonText: "新建会话",
      cancelButtonText: "留在当前会话",
      confirmButtonColor: "oklch(48% 0.12 250)",
    });
    await newSession();
  } catch {
    // 保留当前 Session。
  }
}

onMounted(() => void initialize());
</script>

<template>
  <div class="app-shell">
    <AppHeader
      :connection="connection"
      :stream-connected="streamConnected"
      :run-status="runStatus"
      :session-status="sessionStatus"
      :mode="gatewayMode"
      :model="model"
      :workspace="session?.workspace ?? null"
      :stop-reason="summary?.stopReason ?? null"
      :busy="actionPending"
      @new-session="confirmNewSession"
      @history="toggleHistory"
    />

    <div v-if="gatewayMode === 'mock'" class="environment-notice" role="status">
      界面演示模式：协议与状态机保持一致，但不会读写磁盘或调用模型。
    </div>

    <div v-if="errorMessage" class="error-banner" role="alert">
      <span aria-hidden="true">!</span>
      <p>{{ errorMessage }}</p>
      <button type="button" aria-label="关闭错误提示" @click="dismissError">关闭</button>
    </div>

    <main class="conversation-main">
      <section
        class="conversation-surface"
        :class="{ 'is-launcher': !session && !viewingHistory }"
        aria-label="hako 工程对话"
      >
        <div v-if="!session && !viewingHistory" class="launcher-view">
          <div class="launcher-copy">
            <h1>开始一个 Coding Task</h1>
            <p>选择工作区，然后描述任务。</p>
          </div>
          <TaskComposer
            :active="isActive"
            :busy="actionPending"
            :disabled="false"
            :mode="gatewayMode"
            :session="session"
            @start="submitSession"
            @continue="submitRun"
            @cancel="confirmCancel"
          />
        </div>

        <template v-else>
          <div v-if="viewingHistory && selectedHistory" class="history-view-banner">
            <div>
              <strong>历史 Session · 只读</strong>
              <span>{{ selectedHistory.workspace }} · {{ selectedHistory.runCount }} Run</span>
            </div>
            <button type="button" @click="closeHistoryView">返回当前会话</button>
          </div>

          <RunTimeline :events="displayedEvents" :active="isActive && !viewingHistory" />

          <div class="conversation-dock">
            <ApprovalPanel
              v-if="pendingApproval && !viewingHistory"
              :approval="pendingApproval"
              :busy="approvalPending"
              @resolve="resolveApproval"
            />
            <TaskComposer
              :active="isActive"
              :busy="actionPending"
              :disabled="viewingHistory"
              :mode="gatewayMode"
              :session="session"
              @start="submitSession"
              @continue="submitRun"
              @cancel="confirmCancel"
            />
          </div>
        </template>
      </section>
    </main>

    <SessionHistoryDrawer
      :open="historyOpen"
      :items="historyItems"
      :busy="actionPending"
      @close="toggleHistory"
      @select="openHistory"
    />
  </div>
</template>
