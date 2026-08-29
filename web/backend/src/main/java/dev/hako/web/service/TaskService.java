package dev.hako.web.service;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;
import dev.hako.web.api.ApiException;
import dev.hako.web.api.ApiModels.ApprovalDecision;
import dev.hako.web.api.ApiModels.ApprovalResponse;
import dev.hako.web.api.ApiModels.CancelResponse;
import dev.hako.web.api.ApiModels.CreateTaskRequest;
import dev.hako.web.api.ApiModels.HealthResponse;
import dev.hako.web.api.ApiModels.WorkerHealth;
import dev.hako.web.config.WebProperties;
import dev.hako.web.domain.BufferedEvent;
import dev.hako.web.domain.TaskState;
import dev.hako.web.domain.TaskStatus;
import dev.hako.web.worker.SecretRedactor;
import dev.hako.web.worker.WorkerLauncher;
import dev.hako.web.worker.WorkerListener;
import dev.hako.web.worker.WorkerSession;
import jakarta.annotation.PreDestroy;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.time.Instant;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@Service
public class TaskService {
    private static final String PROTOCOL_VERSION = "1.0";
    private static final Set<String> WORKER_TYPES = Set.of(
            "ready",
            "event",
            "approval_required",
            "approval_resolved",
            "result",
            "fatal");
    private static final Set<String> HAKO_EVENT_TYPES = Set.of(
            "run_started",
            "turn_started",
            "assistant_text",
            "tool_call_started",
            "tool_call_finished",
            "context_stats",
            "verification_required",
            "continuation_required",
            "subagent_started",
            "subagent_finished",
            "run_finished",
            "agent_error");
    private static final Set<String> SUCCESS_REASONS = Set.of(
            "done_read_only",
            "done_verified");

    private final Object lock = new Object();
    private final WebProperties properties;
    private final ObjectMapper mapper;
    private final WorkerLauncher launcher;
    private final ExecutorService controlExecutor = Executors.newSingleThreadExecutor(
            Thread.ofPlatform().name("hako-web-control-", 0).factory());
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(
            Thread.ofPlatform().name("hako-web-timer-", 0).factory());
    private TaskState current;

    public TaskService(
            WebProperties properties,
            ObjectMapper mapper,
            WorkerLauncher launcher) {
        this.properties = properties;
        this.mapper = mapper;
        this.launcher = launcher;
        scheduler.scheduleAtFixedRate(this::heartbeat, 15, 15, TimeUnit.SECONDS);
    }

    public ObjectNode createTask(CreateTaskRequest request) {
        String prompt = request.prompt().trim();
        int maxSteps = request.options() == null ? 40 : request.options().valueOrDefault();
        Path workspace = validateWorkspace(request.workspace());
        TaskState task;
        synchronized (lock) {
            if (current != null && current.status.isActive()) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "TASK_CONFLICT",
                        "当前已有任务正在运行。") ;
            }
            task = new TaskState(UUID.randomUUID(), workspace, prompt, maxSteps);
            current = task;
            transitionLocked(task, TaskStatus.STARTING, "任务已登记，正在启动 Worker");
            controlExecutor.submit(() -> launch(task));
            return task.resource(mapper);
        }
    }

    public ObjectNode getTask(UUID taskId) {
        synchronized (lock) {
            return requireTask(taskId).resource(mapper);
        }
    }

    public ObjectNode getSummary(UUID taskId) {
        synchronized (lock) {
            TaskState task = requireTask(taskId);
            if (!task.status.isTerminal()) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "TASK_NOT_FINISHED",
                        "任务仍在运行，摘要尚不可用。") ;
            }
            if (task.summary == null) {
                throw new ApiException(
                        HttpStatus.INTERNAL_SERVER_ERROR,
                        "INTERNAL_ERROR",
                        "终态任务缺少结构化摘要。") ;
            }
            return task.summary.deepCopy();
        }
    }

    public ApprovalResponse respondApproval(
            UUID taskId,
            UUID approvalId,
            ApprovalDecision decision) {
        synchronized (lock) {
            TaskState task = requireTask(taskId);
            if (task.status != TaskStatus.WAITING_APPROVAL || task.pendingApproval == null) {
                throw new ApiException(
                        HttpStatus.NOT_FOUND,
                        "APPROVAL_NOT_FOUND",
                        "当前任务没有待处理审批。") ;
            }
            UUID pendingId = parseUuid(task.pendingApproval.path("approvalId").asText(), "approvalId");
            if (!pendingId.equals(approvalId)) {
                throw new ApiException(
                        HttpStatus.NOT_FOUND,
                        "APPROVAL_NOT_FOUND",
                        "审批不属于当前待处理操作。") ;
            }
            if (task.approvalResponseSent) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "APPROVAL_ALREADY_RESOLVED",
                        "审批决定已经发送给 Worker。") ;
            }
            if (!containsText(task.pendingApproval.path("allowedDecisions"), decision.name())) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "DECISION_NOT_ALLOWED",
                        "当前风险等级不允许该审批决定。") ;
            }
            if (task.worker == null || !task.worker.isAlive()) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "INVALID_STATE",
                        "Worker 已退出，无法处理审批。") ;
            }

            Instant acceptedAt = Instant.now();
            ObjectNode message = mapper.createObjectNode();
            message.put("protocolVersion", PROTOCOL_VERSION);
            message.put("type", "approval_response");
            message.put("requestId", UUID.randomUUID().toString());
            ObjectNode payload = message.putObject("payload");
            payload.put("taskId", task.taskId.toString());
            payload.put("approvalId", approvalId.toString());
            payload.put("decision", decision.name());
            try {
                task.worker.send(message);
            } catch (IOException exc) {
                failLocked(task, "WORKER_IO_ERROR", "无法把审批决定发送给 Worker。", null);
                throw new ApiException(
                        HttpStatus.INTERNAL_SERVER_ERROR,
                        "INTERNAL_ERROR",
                        "审批发送失败。") ;
            }
            task.approvalResponseSent = true;
            task.sentApprovalDecision = decision.name();
            return new ApprovalResponse(
                    PROTOCOL_VERSION,
                    taskId,
                    approvalId,
                    "ACCEPTED",
                    decision,
                    acceptedAt);
        }
    }

    public CancelResponse cancelTask(UUID taskId) {
        synchronized (lock) {
            TaskState task = requireTask(taskId);
            if (task.status == TaskStatus.CANCELLED) {
                return new CancelResponse(
                        PROTOCOL_VERSION,
                        taskId,
                        TaskStatus.CANCELLED.name(),
                        "任务已经取消；已有修改没有自动回滚。");
            }
            if (task.status == TaskStatus.CANCELLING) {
                return cancellingResponse(taskId);
            }
            if (!task.status.isActive()) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "INVALID_STATE",
                        "已结束的任务不能取消。") ;
            }

            task.pendingApproval = null;
            transitionLocked(task, TaskStatus.CANCELLING, "正在终止 Worker 进程树");
            WorkerSession worker = task.worker;
            if (worker == null) {
                controlExecutor.submit(() -> finishCancellation(task, false));
            } else {
                worker.terminate(
                        properties.getKillGracePeriod(),
                        forced -> controlExecutor.submit(() -> finishCancellation(task, forced)));
            }
            return cancellingResponse(taskId);
        }
    }

    public SseEmitter subscribe(UUID taskId, Long lastEventId) {
        SseEmitter emitter = new SseEmitter(0L);
        synchronized (lock) {
            TaskState task = requireTask(taskId);
            long requestedAfter = lastEventId == null ? 0L : lastEventId;
            if (requestedAfter < 0) {
                throw new ApiException(
                        HttpStatus.BAD_REQUEST,
                        "INVALID_REQUEST",
                        "Last-Event-ID 必须是非负整数。") ;
            }
            long oldest = task.oldestEventId();
            try {
                if (requestedAfter > 0 && requestedAfter < oldest - 1) {
                    sendGap(emitter, task.gapEnvelope(mapper, requestedAfter));
                }
                for (BufferedEvent event : task.eventsAfter(requestedAfter)) {
                    sendEvent(emitter, event);
                }
                if (task.status.isTerminal()) {
                    emitter.complete();
                    return emitter;
                }
                task.subscribers.add(emitter);
                emitter.onCompletion(() -> removeSubscriber(task, emitter));
                emitter.onTimeout(() -> removeSubscriber(task, emitter));
                emitter.onError(error -> removeSubscriber(task, emitter));
                return emitter;
            } catch (IOException exc) {
                emitter.completeWithError(exc);
                return emitter;
            }
        }
    }

    public HealthResponse health() {
        boolean pythonConfigured = properties.getPythonExecutable() != null
                && !properties.getPythonExecutable().isBlank();
        Path root = properties.getRepositoryRoot().toAbsolutePath().normalize();
        Path configured = properties.getWorkerEntrypoint();
        Path entrypoint = configured.isAbsolute() ? configured : root.resolve(configured).normalize();
        return new HealthResponse(
                PROTOCOL_VERSION,
                "UP",
                "0.1.0",
                new WorkerHealth(pythonConfigured, Files.isRegularFile(entrypoint) && Files.isReadable(entrypoint)));
    }

    private void launch(TaskState task) {
        try {
            WorkerSession worker = launcher.launch(new WorkerListener() {
                @Override
                public void onMessage(JsonNode message) {
                    controlExecutor.submit(() -> handleWorkerMessage(task, message));
                }

                @Override
                public void onProtocolError(String message) {
                    controlExecutor.submit(() -> protocolFailure(task, message));
                }

                @Override
                public void onExit(int exitCode, String stderrTail) {
                    controlExecutor.submit(() -> workerExited(task, exitCode, stderrTail));
                }
            });
            synchronized (lock) {
                if (current != task || task.status != TaskStatus.STARTING) {
                    worker.close();
                    return;
                }
                task.worker = worker;
            }
            scheduler.schedule(
                    () -> controlExecutor.submit(() -> startTimedOut(task)),
                    properties.getStartTimeout().toMillis(),
                    TimeUnit.MILLISECONDS);
        } catch (IOException exc) {
            synchronized (lock) {
                failLocked(task, "WORKER_START_FAILED", "无法启动 Python Worker。", null);
            }
        }
    }

    private void handleWorkerMessage(TaskState task, JsonNode message) {
        synchronized (lock) {
            if (current != task || task.status.isTerminal() || task.status == TaskStatus.CANCELLING) {
                return;
            }
            try {
                requireText(message, "protocolVersion", PROTOCOL_VERSION);
                String type = requireText(message, "type", null);
                if (!WORKER_TYPES.contains(type)) {
                    throw new WorkerProtocolException("未知 Worker 消息类型：" + type);
                }
                if ("ready".equals(type)) {
                    handleReadyLocked(task, message);
                    return;
                }
                validateTaskMessageLocked(task, message);
                switch (type) {
                    case "event" -> handleHakoEventLocked(task, requireObject(message, "payload"));
                    case "approval_required" -> handleApprovalRequiredLocked(task, requireObject(message, "payload"));
                    case "approval_resolved" -> handleApprovalResolvedLocked(task, requireObject(message, "payload"));
                    case "result" -> handleResultLocked(task, requireObject(message, "payload"));
                    case "fatal" -> handleFatalLocked(task, requireObject(message, "payload"));
                    default -> throw new WorkerProtocolException("未处理 Worker 消息：" + type);
                }
            } catch (WorkerProtocolException exc) {
                failLocked(task, "WORKER_PROTOCOL_ERROR", exc.getMessage(), null);
            }
        }
    }

    private void handleReadyLocked(TaskState task, JsonNode message) {
        if (task.workerReady || task.startSent || task.status != TaskStatus.STARTING) {
            throw new WorkerProtocolException("Worker ready 出现在非法状态。") ;
        }
        if (!message.path("workerPid").canConvertToLong()) {
            throw new WorkerProtocolException("Worker ready 缺少 workerPid。") ;
        }
        JsonNode capabilities = message.path("capabilities");
        if (!capabilities.isArray()
                || !containsText(capabilities, "events")
                || !containsText(capabilities, "approval")
                || !containsText(capabilities, "run_result")) {
            throw new WorkerProtocolException("Worker capabilities 不完整。") ;
        }
        task.workerReady = true;
        task.startedAt = Instant.now();
        sendStartLocked(task);
    }

    private void sendStartLocked(TaskState task) {
        if (task.worker == null) {
            throw new WorkerProtocolException("Worker ready 早于进程会话登记。") ;
        }
        ObjectNode message = mapper.createObjectNode();
        message.put("protocolVersion", PROTOCOL_VERSION);
        message.put("type", "start");
        message.put("requestId", UUID.randomUUID().toString());
        ObjectNode payload = message.putObject("payload");
        payload.put("taskId", task.taskId.toString());
        payload.put("workspace", task.workspace.toString());
        payload.put("prompt", task.prompt);
        payload.put("maxSteps", task.maxSteps);
        try {
            task.worker.send(message);
            task.startSent = true;
        } catch (IOException exc) {
            throw new WorkerProtocolException("无法发送 Worker start 消息。") ;
        }
    }

    private void validateTaskMessageLocked(TaskState task, JsonNode message) {
        requireText(message, "taskId", task.taskId.toString());
        JsonNode sequence = message.path("sequence");
        if (!sequence.canConvertToLong() || sequence.longValue() != task.expectedWorkerSequence) {
            throw new WorkerProtocolException(
                    "Worker sequence 不连续，期望 "
                            + task.expectedWorkerSequence
                            + "，收到 "
                            + sequence.asText("null")
                            + "。") ;
        }
        task.expectedWorkerSequence += 1;
    }

    private void handleHakoEventLocked(TaskState task, ObjectNode payload) {
        String kind = requireText(payload, "kind", null);
        if (!HAKO_EVENT_TYPES.contains(kind)) {
            throw new WorkerProtocolException("未知 hako 事件类型：" + kind);
        }
        ObjectNode data = requireObject(payload, "data");
        publishLocked(task, kind, "HAKO", data);

        switch (kind) {
            case "run_started" -> {
                if (task.status != TaskStatus.STARTING) {
                    throw new WorkerProtocolException("run_started 出现在非法状态。") ;
                }
                transitionLocked(task, TaskStatus.RUNNING, "Agent 已开始运行");
            }
            case "turn_started" -> task.step = requireInt(data, "step");
            case "context_stats" -> {
                task.usedTokens = requireInt(data, "usedTokens");
                task.contextLimit = requireInt(data, "limit");
                task.messageCount = requireInt(data, "messageCount");
            }
            case "run_finished" -> task.runFinished = data.deepCopy();
            default -> {
                // 其余业务事件只进入时间线，不改变 Web 状态。
            }
        }
    }

    private void handleApprovalRequiredLocked(TaskState task, ObjectNode payload) {
        if (task.status != TaskStatus.RUNNING || task.pendingApproval != null) {
            throw new WorkerProtocolException("approval_required 出现在非法状态。") ;
        }
        UUID approvalId = parseUuid(requireText(payload, "approvalId", null), "approvalId");
        ObjectNode tool = requireObject(payload, "tool");
        requireText(tool, "name", null);
        if (!tool.path("args").isObject()) {
            throw new WorkerProtocolException("approval_required.tool.args 必须是对象。") ;
        }
        String risk = requireText(payload, "riskLevel", null);
        if (!Set.of("NORMAL", "HIGH").contains(risk)) {
            throw new WorkerProtocolException("approval_required.riskLevel 非法。") ;
        }
        JsonNode allowed = payload.path("allowedDecisions");
        if (!allowed.isArray() || allowed.isEmpty()) {
            throw new WorkerProtocolException("approval_required.allowedDecisions 不能为空。") ;
        }
        Set<String> decisions = new HashSet<>();
        allowed.forEach(value -> decisions.add(value.asText()));
        if (!Set.of("ALLOW_ONCE", "ALLOW_SESSION", "DENY").containsAll(decisions)
                || ("HIGH".equals(risk) && decisions.contains("ALLOW_SESSION"))) {
            throw new WorkerProtocolException("approval_required.allowedDecisions 越权。") ;
        }

        ObjectNode approval = mapper.createObjectNode();
        approval.put("approvalId", approvalId.toString());
        approval.put("taskId", task.taskId.toString());
        approval.put("status", "PENDING");
        approval.set("tool", tool.deepCopy());
        approval.put("riskLevel", risk);
        if (payload.path("dangerReason").isNull() || payload.path("dangerReason").isMissingNode()) {
            approval.putNull("dangerReason");
        } else {
            approval.put("dangerReason", payload.path("dangerReason").asText());
        }
        approval.set("allowedDecisions", allowed.deepCopy());
        approval.put("requestedAt", payload.path("requestedAt").asText(Instant.now().toString()));
        approval.putNull("resolvedAt");
        approval.putNull("decision");
        task.pendingApproval = approval;
        task.approvalResponseSent = false;
        task.sentApprovalDecision = null;
        publishLocked(task, "approval_required", "WORKER", approval);
        transitionLocked(task, TaskStatus.WAITING_APPROVAL, "等待用户批准 " + tool.path("name").asText());
    }

    private void handleApprovalResolvedLocked(TaskState task, ObjectNode payload) {
        if (task.status != TaskStatus.WAITING_APPROVAL
                || task.pendingApproval == null
                || !task.approvalResponseSent) {
            throw new WorkerProtocolException("approval_resolved 出现在非法状态。") ;
        }
        String approvalId = requireText(payload, "approvalId", null);
        String decision = requireText(payload, "decision", null);
        if (!approvalId.equals(task.pendingApproval.path("approvalId").asText())
                || !decision.equals(task.sentApprovalDecision)) {
            throw new WorkerProtocolException("approval_resolved 与已发送决定不一致。") ;
        }
        publishLocked(task, "approval_resolved", "WORKER", payload);
        task.pendingApproval = null;
        task.approvalResponseSent = false;
        task.sentApprovalDecision = null;
        transitionLocked(task, TaskStatus.RUNNING, "Worker 已接收审批决定");
    }

    private void handleResultLocked(TaskState task, ObjectNode payload) {
        if (task.runFinished == null) {
            throw new WorkerProtocolException("result 早于 run_finished。") ;
        }
        boolean success = requireBoolean(payload, "success");
        String stopReason = requireText(payload, "stopReason", null);
        int steps = requireInt(payload, "steps");
        int totalTokens = requireInt(payload, "totalTokens");
        JsonNode changedPaths = payload.path("changedPaths");
        JsonNode verification = payload.path("verification");
        if (!changedPaths.isArray() || !verification.isArray()) {
            throw new WorkerProtocolException("result 的路径和验证字段必须是数组。") ;
        }
        if (success != SUCCESS_REASONS.contains(stopReason)) {
            throw new WorkerProtocolException("result.success 与 stopReason 不一致。") ;
        }
        if (!stopReason.equals(task.runFinished.path("reason").asText())
                || steps != task.runFinished.path("steps").asInt(-1)
                || totalTokens != task.runFinished.path("totalTokens").asInt(-1)
                || !stringValues(changedPaths).equals(stringValues(task.runFinished.path("changedPaths")))) {
            throw new WorkerProtocolException("result 与 run_finished 不一致。") ;
        }

        TaskStatus previous = task.status;
        TaskStatus terminal = success ? TaskStatus.COMPLETED : TaskStatus.FAILED;
        task.status = terminal;
        task.finishedAt = Instant.now();
        task.resultReceived = true;
        task.outcome = payload.deepCopy();
        if (!task.outcome.has("error")) {
            task.outcome.putNull("error");
        }
        task.error = task.outcome.path("error").isObject()
                ? (ObjectNode) task.outcome.path("error").deepCopy()
                : null;
        task.summary = mapper.createObjectNode();
        task.summary.put("schemaVersion", PROTOCOL_VERSION);
        task.summary.put("taskId", task.taskId.toString());
        task.summary.put("status", terminal.name());
        task.summary.setAll(task.outcome);
        task.summary.put("finishedAt", task.finishedAt.toString());
        publishLocked(task, "task_result", "WORKER", task.outcome);
        publishStatusLocked(task, previous, terminal, "Worker 返回权威 RunResult");
        completeSubscribersLocked(task);
        closeWorker(task);
    }

    private void handleFatalLocked(TaskState task, ObjectNode payload) {
        String code = requireText(payload, "code", null);
        String message = requireText(payload, "message", null);
        failLocked(task, code, message, null);
    }

    private void protocolFailure(TaskState task, String message) {
        synchronized (lock) {
            failLocked(task, "WORKER_PROTOCOL_ERROR", message, null);
        }
    }

    private void workerExited(TaskState task, int exitCode, String stderrTail) {
        synchronized (lock) {
            if (current != task || task.status.isTerminal() || task.status == TaskStatus.CANCELLING) {
                return;
            }
            String message = exitCode == 0
                    ? "Worker 在返回 result 前退出。"
                    : "Worker 异常退出（exit=" + exitCode + "）。";
            failLocked(task, "WORKER_EXITED", message, exitCode);
        }
    }

    private void startTimedOut(TaskState task) {
        synchronized (lock) {
            if (current == task && task.status == TaskStatus.STARTING && !task.workerReady) {
                failLocked(task, "WORKER_START_TIMEOUT", "Worker 未在规定时间内发送 ready。", null);
            }
        }
    }

    private void finishCancellation(TaskState task, boolean forced) {
        synchronized (lock) {
            if (current != task || task.status != TaskStatus.CANCELLING) {
                return;
            }
            TaskStatus previous = task.status;
            task.status = TaskStatus.CANCELLED;
            task.finishedAt = Instant.now();
            task.summary = task.cancelledSummary(mapper);
            task.outcome = outcomeFromSummary(task.summary);
            ObjectNode payload = mapper.createObjectNode();
            payload.put("message", "Worker 已停止；已经写入的文件不会自动回滚。");
            payload.put("forced", forced);
            publishLocked(task, "task_cancelled", "WEB", payload);
            publishStatusLocked(task, previous, TaskStatus.CANCELLED, "任务已取消");
            completeSubscribersLocked(task);
            closeWorker(task);
        }
    }

    private void failLocked(TaskState task, String code, String rawMessage, Integer exitCode) {
        if (current != task || task.status.isTerminal() || task.status == TaskStatus.CANCELLING) {
            return;
        }
        String message = SecretRedactor.redact(rawMessage == null ? "Worker 运行失败。" : rawMessage);
        TaskStatus previous = task.status;
        task.status = TaskStatus.FAILED;
        task.finishedAt = Instant.now();
        task.pendingApproval = null;
        task.error = mapper.createObjectNode();
        task.error.put("code", code);
        task.error.put("message", message);
        task.summary = task.failureSummary(mapper, code, message);
        task.outcome = outcomeFromSummary(task.summary);
        ObjectNode payload = mapper.createObjectNode();
        payload.put("code", code);
        payload.put("message", message);
        if (exitCode == null) {
            payload.putNull("exitCode");
        } else {
            payload.put("exitCode", exitCode);
        }
        publishLocked(task, "worker_error", "WORKER", payload);
        publishStatusLocked(task, previous, TaskStatus.FAILED, message);
        completeSubscribersLocked(task);
        if (task.worker != null && task.worker.isAlive()) {
            task.worker.terminate(properties.getKillGracePeriod(), ignored -> {});
        }
    }

    private void transitionLocked(TaskState task, TaskStatus next, String reason) {
        TaskStatus previous = task.status;
        if (previous == next) {
            return;
        }
        task.status = next;
        publishStatusLocked(task, previous, next, reason);
    }

    private void publishStatusLocked(
            TaskState task,
            TaskStatus previous,
            TaskStatus currentStatus,
            String reason) {
        ObjectNode payload = mapper.createObjectNode();
        payload.put("previous", previous.name());
        payload.put("current", currentStatus.name());
        payload.put("reason", reason);
        publishLocked(task, "task_status", "WEB", payload);
    }

    private void publishLocked(
            TaskState task,
            String type,
            String source,
            JsonNode payload) {
        BufferedEvent event = task.appendEvent(
                mapper,
                type,
                source,
                payload,
                properties.getEventMaxCount(),
                properties.getEventMaxBytes().toBytes());
        for (SseEmitter subscriber : List.copyOf(task.subscribers)) {
            try {
                sendEvent(subscriber, event);
            } catch (IOException | IllegalStateException exc) {
                task.subscribers.remove(subscriber);
                subscriber.complete();
            }
        }
    }

    private void heartbeat() {
        synchronized (lock) {
            if (current == null || current.status.isTerminal()) {
                return;
            }
            String comment = "heartbeat " + Instant.now();
            for (SseEmitter subscriber : List.copyOf(current.subscribers)) {
                try {
                    subscriber.send(SseEmitter.event().comment(comment));
                } catch (IOException | IllegalStateException exc) {
                    current.subscribers.remove(subscriber);
                    subscriber.complete();
                }
            }
        }
    }

    private void removeSubscriber(TaskState task, SseEmitter emitter) {
        synchronized (lock) {
            task.subscribers.remove(emitter);
        }
    }

    private void completeSubscribersLocked(TaskState task) {
        for (SseEmitter subscriber : List.copyOf(task.subscribers)) {
            subscriber.complete();
        }
        task.subscribers.clear();
    }

    private static void sendEvent(SseEmitter emitter, BufferedEvent event) throws IOException {
        emitter.send(SseEmitter.event()
                .id(Long.toString(event.eventId()))
                .name(event.type())
                .data(event.envelope()));
    }

    private static void sendGap(SseEmitter emitter, ObjectNode envelope) throws IOException {
        emitter.send(SseEmitter.event().name("stream_gap").data(envelope));
    }

    private TaskState requireTask(UUID taskId) {
        if (current == null || !current.taskId.equals(taskId)) {
            throw new ApiException(HttpStatus.NOT_FOUND, "TASK_NOT_FOUND", "任务不存在。") ;
        }
        return current;
    }

    private Path validateWorkspace(String raw) {
        final Path candidate;
        try {
            candidate = Path.of(raw.trim());
        } catch (InvalidPathException exc) {
            throw new ApiException(
                    HttpStatus.BAD_REQUEST,
                    "INVALID_REQUEST",
                    "workspace 不是合法路径。",
                    Map.of("field", "workspace"));
        }
        if (!candidate.isAbsolute()) {
            throw new ApiException(
                    HttpStatus.BAD_REQUEST,
                    "INVALID_REQUEST",
                    "workspace 必须是绝对路径。",
                    Map.of("field", "workspace"));
        }
        final Path resolved;
        try {
            resolved = candidate.toRealPath();
        } catch (IOException exc) {
            throw new ApiException(
                    HttpStatus.BAD_REQUEST,
                    "INVALID_REQUEST",
                    "workspace 不存在或无法解析。",
                    Map.of("field", "workspace"));
        }
        if (!Files.isDirectory(resolved) || !Files.isReadable(resolved)) {
            throw new ApiException(
                    HttpStatus.BAD_REQUEST,
                    "INVALID_REQUEST",
                    "workspace 必须是可读目录。",
                    Map.of("field", "workspace"));
        }
        Path repositoryRoot = properties.getRepositoryRoot().toAbsolutePath().normalize();
        boolean allowed = properties.getAllowedRoots().stream().anyMatch(root -> {
            Path configured = root.isAbsolute() ? root : repositoryRoot.resolve(root).normalize();
            try {
                return resolved.startsWith(configured.toRealPath());
            } catch (IOException ignored) {
                return false;
            }
        });
        if (!allowed) {
            throw new ApiException(
                    HttpStatus.FORBIDDEN,
                    "WORKSPACE_OUTSIDE_ALLOWED_ROOTS",
                    "workspace 不在允许的本地根目录内。",
                    Map.of("field", "workspace"));
        }
        return resolved;
    }

    private CancelResponse cancellingResponse(UUID taskId) {
        return new CancelResponse(
                PROTOCOL_VERSION,
                taskId,
                TaskStatus.CANCELLING.name(),
                "正在终止 Worker 进程树；已发生的文件修改不会自动回滚。");
    }

    private ObjectNode outcomeFromSummary(ObjectNode summary) {
        ObjectNode outcome = summary.deepCopy();
        outcome.remove(List.of("schemaVersion", "taskId", "status", "finishedAt"));
        return outcome;
    }

    private void closeWorker(TaskState task) {
        if (task.worker != null) {
            task.worker.close();
            task.worker = null;
        }
    }

    private static String requireText(JsonNode node, String field, String expected) {
        JsonNode value = node.path(field);
        if (!value.isTextual() || value.asText().isBlank()) {
            throw new WorkerProtocolException(field + " 必须是非空字符串。") ;
        }
        String actual = value.asText();
        if (expected != null && !expected.equals(actual)) {
            throw new WorkerProtocolException(field + " 不匹配。") ;
        }
        return actual;
    }

    private static ObjectNode requireObject(JsonNode node, String field) {
        JsonNode value = node.path(field);
        if (!value.isObject()) {
            throw new WorkerProtocolException(field + " 必须是对象。") ;
        }
        return (ObjectNode) value;
    }

    private static int requireInt(JsonNode node, String field) {
        JsonNode value = node.path(field);
        if (!value.isIntegralNumber() || !value.canConvertToInt() || value.intValue() < 0) {
            throw new WorkerProtocolException(field + " 必须是非负整数。") ;
        }
        return value.intValue();
    }

    private static boolean requireBoolean(JsonNode node, String field) {
        JsonNode value = node.path(field);
        if (!value.isBoolean()) {
            throw new WorkerProtocolException(field + " 必须是布尔值。") ;
        }
        return value.booleanValue();
    }

    private static UUID parseUuid(String raw, String field) {
        try {
            return UUID.fromString(raw);
        } catch (IllegalArgumentException exc) {
            throw new WorkerProtocolException(field + " 必须是 UUID。") ;
        }
    }

    private static boolean containsText(JsonNode array, String expected) {
        if (!array.isArray()) {
            return false;
        }
        for (JsonNode item : array) {
            if (item.isTextual() && expected.equals(item.asText())) {
                return true;
            }
        }
        return false;
    }

    private static List<String> stringValues(JsonNode array) {
        if (!array.isArray()) {
            throw new WorkerProtocolException("期望字符串数组。") ;
        }
        return java.util.stream.StreamSupport.stream(array.spliterator(), false)
                .map(value -> {
                    if (!value.isTextual()) {
                        throw new WorkerProtocolException("数组元素必须是字符串。") ;
                    }
                    return value.asText();
                })
                .sorted()
                .toList();
    }

    @PreDestroy
    public void shutdown() {
        synchronized (lock) {
            if (current != null && current.worker != null && current.worker.isAlive()) {
                current.worker.close();
            }
        }
        scheduler.shutdownNow();
        controlExecutor.shutdownNow();
    }

    private static final class WorkerProtocolException extends RuntimeException {
        WorkerProtocolException(String message) {
            super(message);
        }
    }
}
