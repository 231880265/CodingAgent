<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import { Skeleton } from "vant";
import type { HakoEvent } from "../types/api";
import EventItem from "./EventItem.vue";

const props = defineProps<{
  events: HakoEvent[];
  active: boolean;
}>();

const scrollArea = ref<HTMLElement | null>(null);
const followTail = ref(true);

watch(
  () => props.events.length,
  async () => {
    if (!followTail.value) return;
    await nextTick();
    const element = scrollArea.value;
    if (element) element.scrollTop = element.scrollHeight;
  },
);

function handleScroll(): void {
  const element = scrollArea.value;
  if (!element) return;
  followTail.value =
    element.scrollHeight - element.scrollTop - element.clientHeight < 80;
}
</script>

<template>
  <div ref="scrollArea" class="timeline-scroll" @scroll="handleScroll">
    <div v-if="events.length" class="timeline-list" aria-live="polite">
      <EventItem v-for="event in events" :key="event.eventId" :event="event" />
    </div>

    <div v-else-if="active" class="timeline-loading" aria-live="polite">
      <Skeleton title :row="4" />
      <p>Worker 正在启动，首条事件到达后会显示在这里。</p>
    </div>

    <div v-else class="timeline-empty">
      <p class="eyebrow">EXECUTION</p>
      <h3>过程会留在这里</h3>
      <p>
        这不是聊天窗口。时间线只记录模型说明、工具调用、审批、验证与结束状态。
      </p>
      <ol class="flow-preview" aria-label="任务执行流程">
        <li><span>01</span>读取仓库与失败信息</li>
        <li><span>02</span>提出受控修改</li>
        <li><span>03</span>运行测试或构建</li>
        <li><span>04</span>根据证据结束</li>
      </ol>
    </div>
  </div>
</template>
