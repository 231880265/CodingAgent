<script setup lang="ts">
import { onMounted } from "vue";
import { showConfirmDialog } from "vant";
import AppHeader from "./components/AppHeader.vue";
import ApprovalPanel from "./components/ApprovalPanel.vue";
import RunTimeline from "./components/RunTimeline.vue";
import SessionSidebar from "./components/SessionSidebar.vue";
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
  initialize,
  startSession,
  createRun,
  resolveApproval,
  cancelRun,
  newSession,
  toggleHistory,
  openHistory,
  deleteHistory,
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
        ? "hako 会先停止当前任务，再挂起这段会话。文件修改会保留，以后仍可从左侧恢复继续。"
        : "当前会话会保存并进入挂起状态。文件修改会保留，以后仍可从左侧恢复继续。",
      confirmButtonText: "新建会话",
      cancelButtonText: "留在当前会话",
      confirmButtonColor: "oklch(48% 0.12 250)",
    });
    await newSession();
  } catch {
    // 保留当前 Session。
  }
}

async function confirmDeleteSession(sessionId: string): Promise<void> {
  try {
    await showConfirmDialog({
      title: "删除这段会话？",
      message: "对话与执行记录会永久删除；工作区文件和已经落盘的修改不会被删除或回滚。",
      confirmButtonText: "删除会话",
      cancelButtonText: "取消",
      confirmButtonColor: "oklch(50% 0.16 28)",
    });
    await deleteHistory(sessionId);
  } catch {
    // 用户保留会话。
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
      :workspace="session?.workspace ?? null"
      :stop-reason="summary?.stopReason ?? null"
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

    <div class="app-body">
      <SessionSidebar
        :open="historyOpen"
        :items="historyItems"
        :busy="actionPending"
        :active-session-id="session?.sessionId ?? null"
        @close="toggleHistory"
        @select="openHistory"
        @delete="confirmDeleteSession"
        @new-session="confirmNewSession"
      />

      <main class="conversation-main">
        <section
          class="conversation-surface"
          :class="{ 'is-launcher': !session }"
          aria-label="hako 工程对话"
        >
          <div v-if="!session" class="launcher-view">
            <div class="launcher-copy">
              <h1>开始一个任务</h1>
              <p>直接提问，或选择工作区处理代码。</p>
            </div>
            <TaskComposer
              :active="isActive"
              :busy="actionPending"
              :disabled="false"
              :mode="gatewayMode"
              :model="model"
              :session="session"
              @start="submitSession"
              @continue="submitRun"
              @cancel="confirmCancel"
            />
          </div>

          <template v-else>
            <RunTimeline :events="displayedEvents" :active="isActive" />

            <div class="conversation-dock">
              <ApprovalPanel
                v-if="pendingApproval"
                :approval="pendingApproval"
                :busy="approvalPending"
                @resolve="resolveApproval"
              />
              <TaskComposer
                :active="isActive"
                :busy="actionPending"
                :disabled="false"
                :mode="gatewayMode"
                :model="model"
                :session="session"
                @start="submitSession"
                @continue="submitRun"
                @cancel="confirmCancel"
              />
            </div>
          </template>
        </section>
      </main>
    </div>
  </div>
</template>
