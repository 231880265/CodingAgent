package dev.hako.web.domain;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;
import dev.hako.web.worker.WorkerSession;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.Iterator;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

public final class TaskState {
    private static final Set<String> CRITICAL_EVENTS = Set.of(
            "task_status",
            "approval_required",
            "approval_resolved",
            "run_finished",
            "task_result",
            "worker_error",
            "task_cancelled",
            "stream_gap");

    public final UUID taskId;
    public final Path workspace;
    public final String prompt;
    public final int maxSteps;
    public final Instant createdAt;
    public final Deque<BufferedEvent> events = new ArrayDeque<>();
    public final List<SseEmitter> subscribers = new ArrayList<>();

    public TaskStatus status = TaskStatus.CREATED;
    public Instant startedAt;
    public Instant finishedAt;
    public Integer step;
    public Integer usedTokens;
    public Integer contextLimit;
    public Integer messageCount;
    public ObjectNode pendingApproval;
    public ObjectNode outcome;
    public ObjectNode error;
    public ObjectNode summary;
    public ObjectNode runFinished;
    public WorkerSession worker;
    public long nextEventId = 1;
    public long expectedWorkerSequence = 1;
    public long eventBytes;
    public boolean workerReady;
    public boolean startSent;
    public boolean resultReceived;
    public boolean approvalResponseSent;
    public String sentApprovalDecision;

    public TaskState(UUID taskId, Path workspace, String prompt, int maxSteps) {
        this.taskId = taskId;
        this.workspace = workspace;
        this.prompt = prompt;
        this.maxSteps = maxSteps;
        this.createdAt = Instant.now();
    }

    public ObjectNode resource(ObjectMapper mapper) {
        ObjectNode root = mapper.createObjectNode();
        root.put("schemaVersion", "1.0");
        root.put("taskId", taskId.toString());
        root.put("status", status.name());
        root.put("workspace", workspace.toString());
        root.put("prompt", prompt);
        root.putObject("options").put("maxSteps", maxSteps);
        root.put("createdAt", createdAt.toString());
        putInstant(root, "startedAt", startedAt);
        putInstant(root, "finishedAt", finishedAt);

        ObjectNode progress = root.putObject("progress");
        putNullable(progress, "step", step);
        progress.put("maxSteps", maxSteps);
        putNullable(progress, "usedTokens", usedTokens);
        putNullable(progress, "contextLimit", contextLimit);
        putNullable(progress, "messageCount", messageCount);
        setNullable(root, "pendingApproval", pendingApproval);
        setNullable(root, "outcome", outcome);
        setNullable(root, "error", error);

        ObjectNode links = root.putObject("links");
        String base = "/api/v1/tasks/" + taskId;
        links.put("self", base);
        links.put("events", base + "/events");
        links.put("summary", base + "/summary");
        return root;
    }

    public BufferedEvent appendEvent(
            ObjectMapper mapper,
            String type,
            String source,
            JsonNode payload,
            int maxCount,
            long maxBytes) {
        long eventId = nextEventId++;
        ObjectNode envelope = mapper.createObjectNode();
        envelope.put("schemaVersion", "1.0");
        envelope.put("eventId", eventId);
        envelope.put("taskId", taskId.toString());
        envelope.put("type", type);
        envelope.put("source", source);
        envelope.put("occurredAt", Instant.now().toString());
        envelope.set("payload", payload == null ? mapper.createObjectNode() : payload.deepCopy());
        int bytes = envelope.toString().getBytes(StandardCharsets.UTF_8).length;
        BufferedEvent event = new BufferedEvent(
                eventId,
                type,
                envelope,
                bytes,
                CRITICAL_EVENTS.contains(type));
        events.addLast(event);
        eventBytes += bytes;
        trim(maxCount, maxBytes);
        return event;
    }

    private void trim(int maxCount, long maxBytes) {
        while (events.size() > maxCount || eventBytes > maxBytes) {
            BufferedEvent removed = removeOldestNonCritical();
            if (removed == null) {
                removed = events.pollFirst();
            }
            if (removed == null) {
                return;
            }
            eventBytes -= removed.serializedBytes();
        }
    }

    private BufferedEvent removeOldestNonCritical() {
        Iterator<BufferedEvent> iterator = events.iterator();
        while (iterator.hasNext()) {
            BufferedEvent candidate = iterator.next();
            if (!candidate.critical()) {
                iterator.remove();
                return candidate;
            }
        }
        return null;
    }

    public List<BufferedEvent> eventsAfter(long eventId) {
        return events.stream().filter(event -> event.eventId() > eventId).toList();
    }

    public long oldestEventId() {
        BufferedEvent first = events.peekFirst();
        return first == null ? nextEventId : first.eventId();
    }

    public ObjectNode gapEnvelope(ObjectMapper mapper, long requestedAfter) {
        ObjectNode root = mapper.createObjectNode();
        root.put("schemaVersion", "1.0");
        root.put("eventId", 0);
        root.put("taskId", taskId.toString());
        root.put("type", "stream_gap");
        root.put("source", "WEB");
        root.put("occurredAt", Instant.now().toString());
        ObjectNode payload = root.putObject("payload");
        payload.put("requestedAfter", requestedAfter);
        payload.put("oldestAvailable", oldestEventId());
        payload.put("reason", "早期事件已从内存缓冲区淘汰。") ;
        return root;
    }

    public ObjectNode cancelledSummary(ObjectMapper mapper) {
        ObjectNode result = mapper.createObjectNode();
        result.put("schemaVersion", "1.0");
        result.put("taskId", taskId.toString());
        result.put("status", TaskStatus.CANCELLED.name());
        result.put("success", false);
        result.putNull("stopReason");
        result.put("steps", step == null ? 0 : step);
        result.put("totalTokens", usedTokens == null ? 0 : usedTokens);
        result.put("finalText", "任务由用户取消。已发生的文件修改没有自动回滚。");
        result.set("changedPaths", mapper.createArrayNode());
        result.set("verification", mapper.createArrayNode());
        result.putNull("error");
        result.put("finishedAt", finishedAt.toString());
        return result;
    }

    public ObjectNode failureSummary(
            ObjectMapper mapper,
            String code,
            String message) {
        ObjectNode failure = mapper.createObjectNode();
        failure.put("schemaVersion", "1.0");
        failure.put("taskId", taskId.toString());
        failure.put("status", TaskStatus.FAILED.name());
        failure.put("success", false);
        failure.putNull("stopReason");
        failure.put("steps", step == null ? 0 : step);
        failure.put("totalTokens", usedTokens == null ? 0 : usedTokens);
        failure.put("finalText", "Worker 未能返回有效的 Agent 结果。");
        failure.set("changedPaths", mapper.createArrayNode());
        failure.set("verification", mapper.createArrayNode());
        ObjectNode failureError = failure.putObject("error");
        failureError.put("code", code);
        failureError.put("message", message);
        failure.put("finishedAt", finishedAt.toString());
        return failure;
    }

    public static ArrayNode stringArray(ObjectMapper mapper, JsonNode source) {
        ArrayNode result = mapper.createArrayNode();
        if (source != null && source.isArray()) {
            source.forEach(item -> result.add(item.asText()));
        }
        return result;
    }

    private static void putNullable(ObjectNode node, String field, Integer value) {
        if (value == null) {
            node.putNull(field);
        } else {
            node.put(field, value);
        }
    }

    private static void putInstant(ObjectNode node, String field, Instant value) {
        if (value == null) {
            node.putNull(field);
        } else {
            node.put(field, value.toString());
        }
    }

    private static void setNullable(ObjectNode node, String field, JsonNode value) {
        if (value == null) {
            node.putNull(field);
        } else {
            node.set(field, value.deepCopy());
        }
    }
}
