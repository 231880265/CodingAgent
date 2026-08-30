<script setup lang="ts">
import { computed } from "vue";
import type { HakoEvent } from "../types/api";
import { deriveRunPresentation } from "../utils/runPresentation";
import AnalysisResult from "./AnalysisResult.vue";
import ConversationResult from "./ConversationResult.vue";
import OutcomeResult from "./OutcomeResult.vue";
import VerifiedResult from "./VerifiedResult.vue";

const props = defineProps<{ event: HakoEvent; events: HakoEvent[] }>();
const view = computed(() => deriveRunPresentation(props.event, props.events));
</script>

<template>
  <ConversationResult
    v-if="view.kind === 'conversation'"
    :event="event"
    :view="view"
  />
  <AnalysisResult
    v-else-if="view.kind === 'analysis'"
    :event="event"
    :view="view"
  />
  <VerifiedResult
    v-else-if="view.kind === 'verified_change'"
    :event="event"
    :view="view"
  />
  <OutcomeResult v-else :event="event" :view="view" />
</template>
