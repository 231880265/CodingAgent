<script setup lang="ts">
import { computed, onMounted } from "vue";
import { Button, Progress, showConfirmDialog } from "vant";
import AppHeader from "./components/AppHeader.vue";
import ApprovalPanel from "./components/ApprovalPanel.vue";
import EvidenceSummary from "./components/EvidenceSummary.vue";
import RunTimeline from "./components/RunTimeline.vue";
import TaskComposer from "./components/TaskComposer.vue";
import { useTaskController } from "./composables/useTaskController";
import type { CreateTaskRequest } from "./types/api";
import { STATUS_LABELS, formatTokens } from "./utils/presentation";

const {
  gatewayMode,
  task,
  events,
  summary,
  model,
  connection,
  streamConnected,
  status,
  isActive,
  pendingApproval,
  contextPercent,
  actionPending,
  approvalPending,
  errorMessage,
  initialize,
  startTask,
  resolveApproval,
  cancelTask,
  dismissError,
} = useTaskController();

const taskTitle = computed(() => {
  if (!task.value) return "等待任务";
  const firstLine = task.value.prompt.split(/\r?\n/, 1)[0]?.trim() ?? "当前任务";
  return firstLine.length > 58 ? `${firstLine.slice(0, 58)}…` : firstLine;
});

const workspaceName = computed(() => {
  if (!task.value) return "尚未选择工作区";
  const parts = task.value.workspace.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? task.value.workspace;
});

async function submitTask(request: CreateTaskRequest): Promise<void> {
  await startTask(request);
}

async function confirmCancel(): Promise<void> {
  try {
    await showConfirmDialog({
      title: "取消当前任务？",
      message: "Worker 会被终止，但已经发生的文件修改不会自动回滚。",
      confirmButtonText: "确认取消",
      cancelButtonText: "继续运行",
      confirmButtonColor: "oklch(52% 0.17 28)",
    });
    await cancelTask();
  } catch {
    // 用户关闭确认框，任务继续运行。
  }
}

onMounted(() => void initialize());
</script>

<template>
  <div class="app-shell">
    <AppHeader
      :connection="connection"
      :stream-connected="streamConnected"
      :status="status"
      :mode="gatewayMode"
      :model="model"
    />

    <div v-if="gatewayMode === 'mock'" class="environment-notice" role="status">
      <span class="notice-label">演示数据</span>
      当前页面使用与 API 同结构的本地事件，不会读取工作区，也不会调用模型。
    </div>

    <div v-if="errorMessage" class="error-banner" role="alert">
      <span aria-hidden="true">!</span>
      <p>{{ errorMessage }}</p>
      <button type="button" aria-label="关闭错误提示" @click="dismissError">关闭</button>
    </div>

    <main class="console-grid">
      <aside class="composer-rail">
        <TaskComposer
          :disabled="isActive"
          :busy="actionPending"
          :mode="gatewayMode"
          :task="task"
          @submit="submitTask"
        />
      </aside>

      <section class="run-panel" aria-labelledby="run-title">
        <header class="run-panel-header">
          <div class="run-identity">
            <p class="workspace-name">{{ workspaceName }}</p>
            <h1 id="run-title">{{ taskTitle }}</h1>
            <p v-if="task" class="workspace-path" :title="task.workspace">
              {{ task.workspace }}
            </p>
          </div>
          <div class="run-controls">
            <span class="status-text" :data-status="status">
              {{ STATUS_LABELS[status] }}
            </span>
            <Button
              v-if="isActive"
              plain
              class="cancel-button"
              :loading="actionPending"
              @click="confirmCancel"
            >
              取消任务
            </Button>
          </div>
        </header>

        <div v-if="task" class="run-progress" aria-label="运行进度">
          <div class="progress-fact">
            <span>回合</span>
            <strong>{{ task.progress.step ?? 0 }} / {{ task.progress.maxSteps }}</strong>
          </div>
          <div class="context-meter">
            <div class="context-labels">
              <span>上下文</span>
              <strong>
                {{ formatTokens(task.progress.usedTokens) }} /
                {{ formatTokens(task.progress.contextLimit) }}
              </strong>
            </div>
            <Progress
              :percentage="contextPercent"
              :show-pivot="false"
              :stroke-width="4"
              color="oklch(55% 0.15 258)"
              track-color="oklch(91% 0.012 255)"
            />
          </div>
          <div class="stream-fact">
            <span class="status-dot" :data-live="streamConnected" aria-hidden="true"></span>
            {{
              streamConnected
                ? "实时事件"
                : isActive
                  ? "等待事件流"
                  : task
                    ? "事件流已结束"
                    : "等待事件流"
            }}
          </div>
        </div>

        <RunTimeline :events="events" :active="isActive" />
      </section>

      <aside class="evidence-rail">
        <ApprovalPanel
          v-if="pendingApproval"
          :approval="pendingApproval"
          :busy="approvalPending"
          @resolve="resolveApproval"
        />
        <div v-else class="approval-clear" aria-label="审批状态">
          <div>
            <p class="eyebrow">APPROVAL</p>
            <strong>无待处理操作</strong>
          </div>
          <span aria-hidden="true">✓</span>
        </div>

        <EvidenceSummary :task="task" :summary="summary" />
      </aside>
    </main>

    <footer class="app-footer">
      <span>hako Web Console · local-first</span>
      <span>浏览器不保存 API Key，模型说明不等于验证证据</span>
    </footer>
  </div>
</template>
