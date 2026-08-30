package dev.hako.web.domain;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;
import dev.hako.web.api.ApiModels.AttachmentInput;
import dev.hako.web.worker.WorkerSession;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

public final class SessionState {
    private static final Set<String> CRITICAL_EVENTS = Set.of(
            "session_status",
            "run_status",
            "approval_required",
            "approval_resolved",
            "run_finished",
            "run_result",
            "worker_error",
            "run_cancelled",
            "worker_exited",
            "stream_gap");

    public final UUID sessionId = UUID.randomUUID();
    public final UUID workerId = UUID.randomUUID();
    public final Path workspace;
    public final Instant createdAt = Instant.now();
    public final Map<UUID, RunState> runs = new LinkedHashMap<>();
    public final Deque<BufferedEvent> events = new ArrayDeque<>();
    public final List<SseEmitter> subscribers = new ArrayList<>();

    public SessionStatus status = SessionStatus.OPENING;
    public Instant closedAt;
    public RunState currentRun;
    public WorkerSession worker;
    public Long workerPid;
    public boolean workerReady;
    public boolean startSent;
    public long nextEventId = 1;
    public long expectedWorkerSequence = 1;
    public long eventBytes;

    public SessionState(
            Path workspace,
            String prompt,
            int maxSteps,
            List<AttachmentInput> attachments) {
        this.workspace = workspace;
        createRun(prompt, maxSteps, attachments);
    }

    public RunState createRun(
            String prompt,
            int maxSteps,
            List<AttachmentInput> attachments) {
        RunState run = new RunState(prompt, maxSteps, attachments);
        runs.put(run.runId, run);
        currentRun = run;
        return run;
    }

    public RunState run(UUID runId) {
        return runs.get(runId);
    }

    public ObjectNode resource(ObjectMapper mapper) {
        ObjectNode root = mapper.createObjectNode();
        root.put("schemaVersion", "1.0");
        root.put("sessionId", sessionId.toString());
        root.put("status", status.name());
        root.put("workspace", workspace.toString());
        root.put("runCount", runs.size());
        root.put(
                "canContinue",
                status == SessionStatus.OPEN
                        && currentRun != null
                        && currentRun.status.isTerminal()
                        && worker != null
                        && worker.isAlive());
        root.put("createdAt", createdAt.toString());
        if (closedAt == null) {
            root.putNull("closedAt");
        } else {
            root.put("closedAt", closedAt.toString());
        }

        ObjectNode workerResource = root.putObject("worker");
        workerResource.put("workerId", workerId.toString());
        if (workerPid == null) {
            workerResource.putNull("pid");
        } else {
            workerResource.put("pid", workerPid);
        }
        workerResource.put("alive", worker != null && worker.isAlive());
        workerResource.put("status", workerStatus());

        if (currentRun == null) {
            root.putNull("currentRun");
        } else {
            root.set("currentRun", currentRun.resource(mapper));
        }

        ObjectNode links = root.putObject("links");
        String base = "/api/v1/sessions/" + sessionId;
        links.put("self", base);
        links.put("events", base + "/events");
        links.put("runs", base + "/runs");
        if (currentRun == null) {
            links.putNull("currentSummary");
        } else {
            links.put(
                    "currentSummary",
                    base + "/runs/" + currentRun.runId + "/summary");
        }
        return root;
    }

    public BufferedEvent appendEvent(
            ObjectMapper mapper,
            UUID runId,
            String type,
            String source,
            JsonNode payload,
            int maxCount,
            long maxBytes) {
        long eventId = nextEventId++;
        ObjectNode envelope = mapper.createObjectNode();
        envelope.put("schemaVersion", "1.0");
        envelope.put("eventId", eventId);
        envelope.put("sessionId", sessionId.toString());
        if (runId != null) {
            envelope.put("runId", runId.toString());
        }
        envelope.put("type", type);
        envelope.put("source", source);
        envelope.put("occurredAt", Instant.now().toString());
        envelope.set(
                "payload",
                payload == null ? mapper.createObjectNode() : payload.deepCopy());
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
        root.put("sessionId", sessionId.toString());
        root.put("type", "stream_gap");
        root.put("source", "WEB");
        root.put("occurredAt", Instant.now().toString());
        ObjectNode payload = root.putObject("payload");
        payload.put("requestedAfter", requestedAfter);
        payload.put("oldestAvailable", oldestEventId());
        payload.put("reason", "早期事件已从内存缓冲区淘汰。");
        return root;
    }

    private String workerStatus() {
        if (status == SessionStatus.CLOSED || status == SessionStatus.FAILED) {
            return "EXITED";
        }
        if (worker != null && worker.isAlive() && workerReady) {
            return "READY";
        }
        return worker == null ? "NOT_STARTED" : "STARTING";
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
}
