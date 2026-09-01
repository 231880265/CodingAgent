package dev.hako.web.service;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;
import dev.hako.web.api.ApiException;
import dev.hako.web.api.ApiModels.ApprovalDecision;
import dev.hako.web.api.ApiModels.ApprovalResponse;
import dev.hako.web.api.ApiModels.AttachmentInput;
import dev.hako.web.api.ApiModels.CancelResponse;
import dev.hako.web.api.ApiModels.CreateRunRequest;
import dev.hako.web.api.ApiModels.CreateSessionRequest;
import dev.hako.web.api.ApiModels.HealthResponse;
import dev.hako.web.api.ApiModels.RunOptions;
import dev.hako.web.api.ApiModels.SessionCloseResponse;
import dev.hako.web.api.ApiModels.SessionSuspendResponse;
import dev.hako.web.api.ApiModels.WorkerHealth;
import dev.hako.web.config.WebProperties;
import dev.hako.web.domain.BufferedEvent;
import dev.hako.web.domain.RunState;
import dev.hako.web.domain.RunStatus;
import dev.hako.web.domain.SessionState;
import dev.hako.web.domain.SessionStatus;
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
public class SessionService {
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
            "acceptance_planned",
            "acceptance_required",
            "verification_required",
            "continuation_required",
            "subagent_started",
            "subagent_finished",
            "run_finished",
            "agent_error");
    private static final Set<String> SUCCESS_REASONS = Set.of(
            "done_read_only",
            "done_verified");
    private static final Set<String> TEXT_MEDIA_TYPES = Set.of(
            "application/json",
            "application/xml",
            "application/yaml",
            "application/x-yaml",
            "application/toml",
            "application/javascript");

    private final Object lock = new Object();
    private final WebProperties properties;
    private final ObjectMapper mapper;
    private final WorkerLauncher launcher;
    private final SessionHistoryRepository history;
    private final RunMemoryBuilder memoryBuilder;
    private final ExecutorService controlExecutor = Executors.newSingleThreadExecutor(
            Thread.ofPlatform().name("hako-web-control-", 0).factory());
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(
            Thread.ofPlatform().name("hako-web-timer-", 0).factory());
    private SessionState current;

    public SessionService(
            WebProperties properties,
            ObjectMapper mapper,
            WorkerLauncher launcher,
            SessionHistoryRepository history) {
        this.properties = properties;
        this.mapper = mapper;
        this.launcher = launcher;
        this.history = history;
        this.memoryBuilder = new RunMemoryBuilder(mapper);
        scheduler.scheduleAtFixedRate(this::heartbeat, 15, 15, TimeUnit.SECONDS);
    }

    public ObjectNode createSession(CreateSessionRequest request) {
        String prompt = request.prompt().trim();
        int maxSteps = request.options() == null
                ? RunOptions.DEFAULT_WEB_SAFETY_BUDGET
                : request.options().valueOrDefault();
        Path workspace = validateWorkspace(request.workspace());
        List<AttachmentInput> attachments = validateAttachments(request.attachments());
        synchronized (lock) {
            if (current != null && !current.status.isDetached()) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "SESSION_CONFLICT",
                        "当前 Session 尚未挂起；请先完成挂起流程再新建会话。");
            }
            if (current != null) {
                completeSubscribersLocked(current);
                closeWorker(current);
            }
            SessionState session = new SessionState(
                    workspace,
                    prompt,
                    maxSteps,
                    attachments);
            current = session;
            history.saveSession(session);
            history.saveRun(session, session.currentRun);
            publishSessionStatusLocked(session, null, SessionStatus.OPENING, "正在启动专属 Worker");
            publishRunStatusLocked(session, session.currentRun, null, RunStatus.PENDING, "Run 已排队");
            controlExecutor.submit(() -> launch(session));
            return session.resource(mapper);
        }
    }

    public ObjectNode getSession(UUID sessionId) {
        synchronized (lock) {
            return requireSession(sessionId).resource(mapper);
        }
    }

    public ObjectNode listHistory() {
        return history.listSessions();
    }

    public ObjectNode getHistory(UUID sessionId) {
        ObjectNode stored = history.getHistory(sessionId);
        if (stored == null) {
            throw new ApiException(
                    HttpStatus.NOT_FOUND,
                    "SESSION_NOT_FOUND",
                    "历史 Session 不存在。");
        }
        return stored;
    }

    public void deleteSession(UUID sessionId) {
        synchronized (lock) {
            if (history.getHistory(sessionId) == null) {
                throw new ApiException(
                        HttpStatus.NOT_FOUND,
                        "SESSION_NOT_FOUND",
                        "历史 Session 不存在。");
            }
            if (current != null && current.sessionId.equals(sessionId)) {
                if (current.currentRun != null && current.currentRun.status.isActive()) {
                    throw new ApiException(
                            HttpStatus.CONFLICT,
                            "RUN_CONFLICT",
                            "当前任务仍在运行，请先停止本轮再删除会话。");
                }
                SessionState removed = current;
                current = null;
                completeSubscribersLocked(removed);
                closeWorker(removed);
            }
            history.deleteSession(sessionId);
        }
    }

    public ObjectNode createRun(UUID sessionId, CreateRunRequest request) {
        String prompt = request.prompt().trim();
        List<AttachmentInput> attachments = validateAttachments(request.attachments());
        synchronized (lock) {
            SessionState session = requireSession(sessionId);
            if (session.status != SessionStatus.OPEN) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "SESSION_NOT_OPEN",
                        "只有 OPEN Session 可以创建 Run。");
            }
            if (session.currentRun != null && session.currentRun.status.isActive()) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "RUN_CONFLICT",
                        "当前 Run 仍在运行，请等待结束后再继续对话。");
            }
            if (session.worker == null || !session.worker.isAlive() || !session.workerReady) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "SESSION_WORKER_UNAVAILABLE",
                        "Session 的 Worker 已不可用，请新建会话。");
            }
            int fallback = session.currentRun == null
                    ? RunOptions.DEFAULT_WEB_SAFETY_BUDGET
                    : session.currentRun.maxSteps;
            int maxSteps = request.options() == null
                    ? fallback
                    : request.options().valueOrDefault();
            RunState run = session.createRun(prompt, maxSteps, attachments);
            publishRunStatusLocked(
                    session,
                    run,
                    null,
                    RunStatus.PENDING,
                    "复用当前 Agent 与 Conversation 创建后续 Run");
            sendRunLocked(session, run);
            return session.resource(mapper);
        }
    }

    public ObjectNode resumeSession(UUID sessionId, CreateRunRequest request) {
        String prompt = request.prompt().trim();
        List<AttachmentInput> attachments = validateAttachments(request.attachments());
        synchronized (lock) {
            if (current != null && !current.status.isDetached()) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "SESSION_CONFLICT",
                        "当前 Session 仍在运行；请先挂起后再恢复其他会话。");
            }
            ObjectNode stored = history.getResumeSnapshot(sessionId);
            if (stored == null) {
                throw new ApiException(
                        HttpStatus.NOT_FOUND,
                        "SESSION_NOT_FOUND",
                        "历史 Session 不存在。");
            }
            if (!SessionStatus.SUSPENDED.name().equals(stored.path("status").asText())) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "SESSION_NOT_SUSPENDED",
                        "只有已挂起的 Session 可以恢复继续。");
            }
            Path workspace = validateWorkspace(stored.path("workspace").asText());
            int fallback = stored.path("lastMaxSteps").asInt(RunOptions.DEFAULT_WEB_SAFETY_BUDGET);
            int maxSteps = request.options() == null
                    ? fallback
                    : request.options().valueOrDefault();
            if (current != null) {
                completeSubscribersLocked(current);
                closeWorker(current);
            }
            SessionState session = new SessionState(
                    sessionId,
                    workspace,
                    parseInstant(stored.path("createdAt").asText(), "createdAt"),
                    stored.path("runCount").asInt(0),
                    stored.path("nextEventId").asLong(1),
                    stored.path("conversation"),
                    stored.path("memorySnapshot"),
                    prompt,
                    maxSteps,
                    attachments);
            current = session;
            history.saveSession(session);
            history.saveRun(session, session.currentRun);
            publishSessionStatusLocked(
                    session,
                    SessionStatus.SUSPENDED,
                    SessionStatus.OPENING,
                    "正在重建 Conversation 并启动新 Worker");
            publishRunStatusLocked(
                    session,
                    session.currentRun,
                    null,
                    RunStatus.PENDING,
                    "恢复历史语义上下文后创建后续 Run");
            controlExecutor.submit(() -> launch(session));
            return session.resource(mapper);
        }
    }

    public ObjectNode getRunSummary(UUID sessionId, UUID runId) {
        synchronized (lock) {
            if (current != null && current.sessionId.equals(sessionId)) {
                RunState run = current.run(runId);
                if (run != null) {
                    if (!run.status.isTerminal()) {
                        throw new ApiException(
                                HttpStatus.CONFLICT,
                                "RUN_NOT_FINISHED",
                                "Run 仍在运行，摘要尚不可用。");
                    }
                    if (run.summary == null) {
                        throw new ApiException(
                                HttpStatus.INTERNAL_SERVER_ERROR,
                                "INTERNAL_ERROR",
                                "终态 Run 缺少结构化摘要。");
                    }
                    return run.summary.deepCopy();
                }
            }
            ObjectNode stored = history.getRunSummary(sessionId, runId);
            if (stored == null) {
                throw new ApiException(
                        HttpStatus.NOT_FOUND,
                        "RUN_NOT_FOUND",
                        "Run 不存在或摘要尚不可用。");
            }
            return stored;
        }
    }

    public ApprovalResponse respondApproval(
            UUID sessionId,
            UUID runId,
            UUID approvalId,
            ApprovalDecision decision) {
        synchronized (lock) {
            SessionState session = requireSession(sessionId);
            RunState run = requireCurrentRun(session, runId);
            if (session.status != SessionStatus.OPEN
                    || run.status != RunStatus.WAITING_APPROVAL
                    || run.pendingApproval == null) {
                throw new ApiException(
                        HttpStatus.NOT_FOUND,
                        "APPROVAL_NOT_FOUND",
                        "当前 Run 没有待处理审批。");
            }
            UUID pendingId = parseUuid(run.pendingApproval.path("approvalId").asText(), "approvalId");
            if (!pendingId.equals(approvalId)) {
                throw new ApiException(
                        HttpStatus.NOT_FOUND,
                        "APPROVAL_NOT_FOUND",
                        "审批不属于当前 Run。");
            }
            if (run.approvalResponseSent) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "APPROVAL_ALREADY_RESOLVED",
                        "审批决定已经发送给 Worker。");
            }
            if (!containsText(run.pendingApproval.path("allowedDecisions"), decision.name())) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "DECISION_NOT_ALLOWED",
                        "当前风险等级不允许该审批决定。");
            }
            ensureWorkerAlive(session);

            Instant acceptedAt = Instant.now();
            ObjectNode message = workerCommand("approval_response");
            ObjectNode payload = message.putObject("payload");
            payload.put("sessionId", session.sessionId.toString());
            payload.put("runId", run.runId.toString());
            payload.put("approvalId", approvalId.toString());
            payload.put("decision", decision.name());
            sendWorkerLocked(session, message, "无法把审批决定发送给 Worker。");
            run.approvalResponseSent = true;
            run.sentApprovalDecision = decision.name();
            return new ApprovalResponse(
                    PROTOCOL_VERSION,
                    sessionId,
                    runId,
                    approvalId,
                    "ACCEPTED",
                    decision,
                    acceptedAt);
        }
    }

    public CancelResponse cancelRun(UUID sessionId, UUID runId) {
        synchronized (lock) {
            SessionState session = requireSession(sessionId);
            RunState run = requireCurrentRun(session, runId);
            if (run.status == RunStatus.CANCELLED) {
                return cancelledResponse(session, run);
            }
            if (run.status == RunStatus.CANCELLING) {
                return cancellingResponse(session, run);
            }
            if (!run.status.isActive()) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "INVALID_STATE",
                        "已结束的 Run 不能取消。");
            }
            if (session.status != SessionStatus.OPEN) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "SESSION_NOT_OPEN",
                        "Session 当前不能接受 Run 取消请求。");
            }
            ensureWorkerAlive(session);
            run.pendingApproval = null;
            transitionRunLocked(session, run, RunStatus.CANCELLING, "正在取消当前 Run");

            ObjectNode message = workerCommand("cancel_run");
            ObjectNode payload = message.putObject("payload");
            payload.put("sessionId", session.sessionId.toString());
            payload.put("runId", run.runId.toString());
            sendWorkerLocked(session, message, "无法把取消请求发送给 Worker。");
            scheduler.schedule(
                    () -> controlExecutor.submit(() -> cancelTimedOut(session, run)),
                    properties.getCancelTimeout().toMillis(),
                    TimeUnit.MILLISECONDS);
            return cancellingResponse(session, run);
        }
    }

    public SessionCloseResponse closeSession(UUID sessionId) {
        synchronized (lock) {
            SessionState session = requireSession(sessionId);
            if (session.status == SessionStatus.CLOSED || session.status == SessionStatus.FAILED) {
                return new SessionCloseResponse(
                        PROTOCOL_VERSION,
                        sessionId,
                        session.status.name());
            }
            if (session.status == SessionStatus.CLOSING) {
                return new SessionCloseResponse(PROTOCOL_VERSION, sessionId, "CLOSING");
            }
            if (session.status == SessionStatus.SUSPENDED) {
                session.closedAt = Instant.now();
                transitionSessionLocked(session, SessionStatus.CLOSED, "Session 已归档");
                completeSubscribersLocked(session);
                return new SessionCloseResponse(PROTOCOL_VERSION, sessionId, "CLOSED");
            }
            if (session.status == SessionStatus.SUSPENDING) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "SESSION_SUSPENDING",
                        "Session 正在挂起，请等待完成后再归档。");
            }
            if (session.currentRun != null && session.currentRun.status.isActive()) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "RUN_CONFLICT",
                        "当前 Run 仍在运行；请先取消并等待 CANCELLED。");
            }
            transitionSessionLocked(session, SessionStatus.CLOSING, "正在关闭 Worker 与会话上下文");
            WorkerSession worker = session.worker;
            if (worker == null || !worker.isAlive()) {
                controlExecutor.submit(() -> finishSessionClose(session, false));
            } else {
                worker.terminate(
                        properties.getKillGracePeriod(),
                        forced -> controlExecutor.submit(() -> finishSessionClose(session, forced)));
            }
            return new SessionCloseResponse(PROTOCOL_VERSION, sessionId, "CLOSING");
        }
    }

    public SessionSuspendResponse suspendSession(UUID sessionId) {
        synchronized (lock) {
            if (current == null || !current.sessionId.equals(sessionId)) {
                ObjectNode stored = history.getHistory(sessionId);
                if (stored == null) {
                    throw new ApiException(
                            HttpStatus.NOT_FOUND,
                            "SESSION_NOT_FOUND",
                            "Session 不存在。");
                }
                String storedStatus = stored.path("status").asText();
                if (SessionStatus.SUSPENDED.name().equals(storedStatus)
                        || SessionStatus.CLOSED.name().equals(storedStatus)
                        || SessionStatus.FAILED.name().equals(storedStatus)) {
                    // 浏览器可能跨过一次后端重启仍保留旧 Session。此时 Worker 已经
                    // 不在当前进程里，历史状态也已持久化；再次挂起应当是幂等操作，
                    // 不能用 SESSION_NOT_FOUND 阻塞用户进入新会话。
                    return new SessionSuspendResponse(
                            PROTOCOL_VERSION,
                            sessionId,
                            storedStatus);
                }
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "SESSION_NOT_ACTIVE",
                        "Session 已不属于当前 Worker，请刷新历史状态后重试。");
            }
            SessionState session = current;
            if (session.status == SessionStatus.SUSPENDED) {
                return new SessionSuspendResponse(PROTOCOL_VERSION, sessionId, "SUSPENDED");
            }
            if (session.status == SessionStatus.SUSPENDING) {
                return new SessionSuspendResponse(PROTOCOL_VERSION, sessionId, "SUSPENDING");
            }
            if (session.status.isTerminal()) {
                return new SessionSuspendResponse(
                        PROTOCOL_VERSION,
                        sessionId,
                        session.status.name());
            }
            if (session.currentRun != null && session.currentRun.status.isActive()) {
                throw new ApiException(
                        HttpStatus.CONFLICT,
                        "RUN_CONFLICT",
                        "当前 Run 仍在运行；请先取消并等待 CANCELLED。");
            }
            transitionSessionLocked(
                    session,
                    SessionStatus.SUSPENDING,
                    "正在保存会话并停止 Worker");
            WorkerSession worker = session.worker;
            if (worker == null || !worker.isAlive()) {
                controlExecutor.submit(() -> finishSessionSuspend(session, false));
            } else {
                worker.terminate(
                        properties.getKillGracePeriod(),
                        forced -> controlExecutor.submit(
                                () -> finishSessionSuspend(session, forced)));
            }
            return new SessionSuspendResponse(PROTOCOL_VERSION, sessionId, "SUSPENDING");
        }
    }

    public SseEmitter subscribe(UUID sessionId, Long lastEventId) {
        SseEmitter emitter = new SseEmitter(0L);
        synchronized (lock) {
            SessionState session = requireSession(sessionId);
            long requestedAfter = lastEventId == null ? 0L : lastEventId;
            if (requestedAfter < 0) {
                throw new ApiException(
                        HttpStatus.BAD_REQUEST,
                        "INVALID_REQUEST",
                        "Last-Event-ID 必须是非负整数。");
            }
            try {
                long oldest = session.oldestEventId();
                if (requestedAfter > 0 && requestedAfter < oldest - 1) {
                    sendGap(emitter, session.gapEnvelope(mapper, requestedAfter));
                }
                for (BufferedEvent event : session.eventsAfter(requestedAfter)) {
                    sendEvent(emitter, event);
                }
                if (session.status.isTerminal() || session.status == SessionStatus.SUSPENDED) {
                    emitter.complete();
                    return emitter;
                }
                session.subscribers.add(emitter);
                emitter.onCompletion(() -> removeSubscriber(session, emitter));
                emitter.onTimeout(() -> removeSubscriber(session, emitter));
                emitter.onError(error -> removeSubscriber(session, emitter));
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
                new WorkerHealth(
                        pythonConfigured,
                        Files.isRegularFile(entrypoint) && Files.isReadable(entrypoint)));
    }

    private void launch(SessionState session) {
        try {
            WorkerSession worker = launcher.launch(new WorkerListener() {
                @Override
                public void onMessage(JsonNode message) {
                    controlExecutor.submit(() -> handleWorkerMessage(session, message));
                }

                @Override
                public void onProtocolError(String message) {
                    controlExecutor.submit(() -> protocolFailure(session, message));
                }

                @Override
                public void onExit(int exitCode, String stderrTail) {
                    controlExecutor.submit(() -> workerExited(session, exitCode, stderrTail));
                }
            });
            synchronized (lock) {
                if (current != session || session.status != SessionStatus.OPENING) {
                    worker.close();
                    return;
                }
                session.worker = worker;
            }
            scheduler.schedule(
                    () -> controlExecutor.submit(() -> startTimedOut(session)),
                    properties.getStartTimeout().toMillis(),
                    TimeUnit.MILLISECONDS);
        } catch (IOException exc) {
            synchronized (lock) {
                failSessionLocked(session, "WORKER_START_FAILED", "无法启动 Python Worker。", null);
            }
        }
    }

    private void handleWorkerMessage(SessionState session, JsonNode message) {
        synchronized (lock) {
            // 旧 Session 的回调在服务端第一道丢弃，不能触碰新 Session。
            if (current != session
                    || session.status.isTerminal()
                    || session.status == SessionStatus.SUSPENDING
                    || session.status == SessionStatus.SUSPENDED) {
                return;
            }
            try {
                requireText(message, "protocolVersion", PROTOCOL_VERSION);
                String type = requireText(message, "type", null);
                if (!WORKER_TYPES.contains(type)) {
                    throw new WorkerProtocolException("未知 Worker 消息类型：" + type);
                }
                if ("ready".equals(type)) {
                    handleReadyLocked(session, message);
                    return;
                }
                RunState run = validateRunMessageLocked(session, message);
                if (run == null || run != session.currentRun || run.status.isTerminal()) {
                    // 同 Session 中上一 Run 的迟到消息也只消费 sequence，不改变状态。
                    return;
                }
                switch (type) {
                    case "event" -> handleHakoEventLocked(session, run, requireObject(message, "payload"));
                    case "approval_required" -> {
                        if (run.status != RunStatus.CANCELLING) {
                            handleApprovalRequiredLocked(session, run, requireObject(message, "payload"));
                        }
                    }
                    case "approval_resolved" -> {
                        if (run.status != RunStatus.CANCELLING) {
                            handleApprovalResolvedLocked(session, run, requireObject(message, "payload"));
                        }
                    }
                    case "result" -> handleResultLocked(session, run, requireObject(message, "payload"));
                    case "fatal" -> handleFatalLocked(session, run, requireObject(message, "payload"));
                    default -> throw new WorkerProtocolException("未处理 Worker 消息：" + type);
                }
            } catch (WorkerProtocolException exc) {
                failSessionLocked(session, "WORKER_PROTOCOL_ERROR", exc.getMessage(), null);
            }
        }
    }

    private void handleReadyLocked(SessionState session, JsonNode message) {
        if (session.workerReady || session.startSent || session.status != SessionStatus.OPENING) {
            throw new WorkerProtocolException("Worker ready 出现在非法状态。");
        }
        if (!message.path("workerPid").canConvertToLong()) {
            throw new WorkerProtocolException("Worker ready 缺少 workerPid。");
        }
        JsonNode capabilities = message.path("capabilities");
        if (!capabilities.isArray()
                || !containsText(capabilities, "events")
                || !containsText(capabilities, "approval")
                || !containsText(capabilities, "run_result")
                || !containsText(capabilities, "multi_run")) {
            throw new WorkerProtocolException("Worker capabilities 不完整。");
        }
        session.workerPid = message.path("workerPid").longValue();
        session.workerReady = true;
        transitionSessionLocked(session, SessionStatus.OPEN, "专属 Worker 已就绪");
        sendStartLocked(session, session.currentRun);
    }

    private void sendStartLocked(SessionState session, RunState run) {
        ObjectNode message = workerCommand("start");
        ObjectNode payload = message.putObject("payload");
        payload.put("sessionId", session.sessionId.toString());
        payload.put("runId", run.runId.toString());
        payload.put("workspace", session.workspace.toString());
        payload.put("prompt", run.prompt);
        payload.put("maxSteps", run.maxSteps);
        if (session.restoredConversation != null && session.restoredConversation.isArray()) {
            payload.set("conversation", session.restoredConversation.deepCopy());
        } else {
            payload.set("conversation", mapper.createArrayNode());
        }
        if (session.restoredMemory != null && session.restoredMemory.isArray()) {
            payload.set("memorySnapshot", session.restoredMemory.deepCopy());
        } else {
            payload.set("memorySnapshot", history.getMemorySnapshot(session.sessionId));
        }
        payload.set(
                "repositoryMemorySnapshot",
                history.getRepositoryMemorySnapshot(session.workspace));
        putAttachments(payload, run.attachments);
        sendWorkerLocked(session, message, "无法发送 Worker start 消息。");
        session.startSent = true;
    }

    private void sendRunLocked(SessionState session, RunState run) {
        ObjectNode message = workerCommand("run");
        ObjectNode payload = message.putObject("payload");
        payload.put("sessionId", session.sessionId.toString());
        payload.put("runId", run.runId.toString());
        payload.put("prompt", run.prompt);
        payload.put("maxSteps", run.maxSteps);
        payload.set("memorySnapshot", history.getMemorySnapshot(session.sessionId));
        payload.set(
                "repositoryMemorySnapshot",
                history.getRepositoryMemorySnapshot(session.workspace));
        putAttachments(payload, run.attachments);
        sendWorkerLocked(session, message, "无法把后续 Run 发送给 Worker。");
    }

    private RunState validateRunMessageLocked(SessionState session, JsonNode message) {
        requireText(message, "sessionId", session.sessionId.toString());
        JsonNode sequence = message.path("sequence");
        if (!sequence.canConvertToLong() || sequence.longValue() != session.expectedWorkerSequence) {
            throw new WorkerProtocolException(
                    "Worker sequence 不连续，期望 "
                            + session.expectedWorkerSequence
                            + "，收到 "
                            + sequence.asText("null")
                            + "。");
        }
        session.expectedWorkerSequence += 1;
        UUID runId = parseUuid(requireText(message, "runId", null), "runId");
        return session.run(runId);
    }

    private void handleHakoEventLocked(
            SessionState session,
            RunState run,
            ObjectNode payload) {
        String kind = requireText(payload, "kind", null);
        if (!HAKO_EVENT_TYPES.contains(kind)) {
            throw new WorkerProtocolException("未知 hako 事件类型：" + kind);
        }
        ObjectNode data = requireObject(payload, "data");
        publishRunEventLocked(session, run, kind, "HAKO", data);

        switch (kind) {
            case "run_started" -> {
                if (run.status == RunStatus.PENDING) {
                    run.startedAt = Instant.now();
                    transitionRunLocked(session, run, RunStatus.RUNNING, "Agent 已开始运行");
                } else if (run.status != RunStatus.CANCELLING) {
                    throw new WorkerProtocolException("run_started 出现在非法状态。");
                }
            }
            case "turn_started" -> run.step = requireInt(data, "step");
            case "context_stats" -> {
                run.usedTokens = requireInt(data, "usedTokens");
                run.contextLimit = requireInt(data, "limit");
                run.messageCount = requireInt(data, "messageCount");
            }
            case "tool_call_finished" -> {
                if (data.path("ok").asBoolean(false) && data.path("touchedPaths").isArray()) {
                    data.path("touchedPaths").forEach(path -> {
                        if (path.isTextual() && !path.asText().isBlank()) {
                            run.changedPaths.add(path.asText());
                        }
                    });
                }
            }
            case "run_finished" -> run.runFinished = data.deepCopy();
            default -> {
                // 其余事件只进入时间线。
            }
        }
    }

    private void handleApprovalRequiredLocked(
            SessionState session,
            RunState run,
            ObjectNode payload) {
        if (run.status != RunStatus.RUNNING || run.pendingApproval != null) {
            throw new WorkerProtocolException("approval_required 出现在非法状态。");
        }
        UUID approvalId = parseUuid(requireText(payload, "approvalId", null), "approvalId");
        ObjectNode tool = requireObject(payload, "tool");
        requireText(tool, "name", null);
        if (!tool.path("args").isObject()) {
            throw new WorkerProtocolException("approval_required.tool.args 必须是对象。");
        }
        String risk = requireText(payload, "riskLevel", null);
        JsonNode allowed = payload.path("allowedDecisions");
        if (!Set.of("NORMAL", "HIGH").contains(risk) || !allowed.isArray() || allowed.isEmpty()) {
            throw new WorkerProtocolException("approval_required 风险字段非法。");
        }
        Set<String> decisions = new HashSet<>();
        allowed.forEach(value -> decisions.add(value.asText()));
        if (!Set.of("ALLOW_ONCE", "ALLOW_SESSION", "DENY").containsAll(decisions)
                || ("HIGH".equals(risk) && decisions.contains("ALLOW_SESSION"))) {
            throw new WorkerProtocolException("approval_required.allowedDecisions 越权。");
        }

        ObjectNode approval = mapper.createObjectNode();
        approval.put("approvalId", approvalId.toString());
        approval.put("sessionId", session.sessionId.toString());
        approval.put("runId", run.runId.toString());
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
        run.pendingApproval = approval;
        run.approvalResponseSent = false;
        run.sentApprovalDecision = null;
        publishRunEventLocked(session, run, "approval_required", "WORKER", approval);
        transitionRunLocked(
                session,
                run,
                RunStatus.WAITING_APPROVAL,
                "等待用户批准 " + tool.path("name").asText());
    }

    private void handleApprovalResolvedLocked(
            SessionState session,
            RunState run,
            ObjectNode payload) {
        if (run.status != RunStatus.WAITING_APPROVAL
                || run.pendingApproval == null
                || !run.approvalResponseSent) {
            throw new WorkerProtocolException("approval_resolved 出现在非法状态。");
        }
        String approvalId = requireText(payload, "approvalId", null);
        String decision = requireText(payload, "decision", null);
        if (!approvalId.equals(run.pendingApproval.path("approvalId").asText())
                || !decision.equals(run.sentApprovalDecision)) {
            throw new WorkerProtocolException("approval_resolved 与已发送决定不一致。");
        }
        publishRunEventLocked(session, run, "approval_resolved", "WORKER", payload);
        run.pendingApproval = null;
        run.approvalResponseSent = false;
        run.sentApprovalDecision = null;
        transitionRunLocked(session, run, RunStatus.RUNNING, "审批决定已作为 observation 返回 Agent");
    }

    private void handleResultLocked(
            SessionState session,
            RunState run,
            ObjectNode payload) {
        if (run.runFinished == null) {
            throw new WorkerProtocolException("result 早于 run_finished。");
        }
        boolean success = requireBoolean(payload, "success");
        String stopReason = requireText(payload, "stopReason", null);
        int steps = requireInt(payload, "steps");
        int totalTokens = requireInt(payload, "totalTokens");
        JsonNode changedPaths = payload.path("changedPaths");
        JsonNode verification = payload.path("verification");
        if (!changedPaths.isArray() || !verification.isArray()) {
            throw new WorkerProtocolException("result 的路径和验证字段必须是数组。");
        }
        if (success != SUCCESS_REASONS.contains(stopReason)) {
            throw new WorkerProtocolException("result.success 与 stopReason 不一致。");
        }
        if (!stopReason.equals(run.runFinished.path("reason").asText())
                || steps != run.runFinished.path("steps").asInt(-1)
                || totalTokens != run.runFinished.path("totalTokens").asInt(-1)
                || !stringValues(changedPaths).equals(stringValues(run.runFinished.path("changedPaths")))) {
            throw new WorkerProtocolException("result 与 run_finished 不一致。");
        }

        if (run.status == RunStatus.CANCELLING) {
            finishRunCancellationLocked(session, run, payload);
            return;
        }
        RunStatus terminal = success ? RunStatus.COMPLETED : RunStatus.FAILED;
        finishRunLocked(session, run, terminal, payload, "Worker 返回权威 RunResult");
    }

    private void handleFatalLocked(
            SessionState session,
            RunState run,
            ObjectNode payload) {
        String code = requireText(payload, "code", null);
        String message = requireText(payload, "message", null);
        if (run.status == RunStatus.CANCELLING) {
            finishRunCancellationLocked(session, run, null);
        } else {
            failRunLocked(session, run, code, message);
        }
        failSessionLocked(session, code, message, null);
    }

    private void finishRunLocked(
            SessionState session,
            RunState run,
            RunStatus terminal,
            ObjectNode payload,
            String reason) {
        if (run.status.isTerminal()) {
            return;
        }
        RunStatus previous = run.status;
        if (!previous.canTransitionTo(terminal)) {
            throw new WorkerProtocolException(
                    "非法 Run 终态迁移：" + previous + " -> " + terminal);
        }
        run.status = terminal;
        run.finishedAt = Instant.now();
        run.resultReceived = true;
        run.outcome = payload.deepCopy();
        if (!run.outcome.has("error")) {
            run.outcome.putNull("error");
        }
        run.error = run.outcome.path("error").isObject()
                ? (ObjectNode) run.outcome.path("error").deepCopy()
                : null;
        run.summary = mapper.createObjectNode();
        run.summary.put("schemaVersion", PROTOCOL_VERSION);
        run.summary.put("sessionId", session.sessionId.toString());
        run.summary.put("runId", run.runId.toString());
        run.summary.put("status", terminal.name());
        run.summary.setAll(run.outcome);
        run.summary.put("finishedAt", run.finishedAt.toString());
        publishRunEventLocked(session, run, "run_result", "WORKER", run.outcome);
        publishRunStatusLocked(session, run, previous, terminal, reason);
        captureRunMemoryLocked(session, run);
    }

    private void finishRunCancellationLocked(
            SessionState session,
            RunState run,
            ObjectNode workerPayload) {
        if (run.status != RunStatus.CANCELLING) {
            return;
        }
        RunStatus previous = run.status;
        run.status = RunStatus.CANCELLED;
        run.finishedAt = Instant.now();
        run.pendingApproval = null;
        if (workerPayload == null) {
            run.summary = run.cancelledSummary(mapper, session.sessionId);
            run.outcome = outcomeFromSummary(run.summary);
        } else {
            run.outcome = workerPayload.deepCopy();
            run.outcome.put("success", false);
            run.outcome.put("stopReason", "cancelled");
            run.summary = mapper.createObjectNode();
            run.summary.put("schemaVersion", PROTOCOL_VERSION);
            run.summary.put("sessionId", session.sessionId.toString());
            run.summary.put("runId", run.runId.toString());
            run.summary.put("status", RunStatus.CANCELLED.name());
            run.summary.setAll(run.outcome);
            run.summary.put("finishedAt", run.finishedAt.toString());
        }
        ObjectNode cancelled = mapper.createObjectNode();
        cancelled.put("message", "Run 已取消；已落盘修改保留，Session 与 Conversation 继续可用。");
        publishRunEventLocked(session, run, "run_cancelled", "WEB", cancelled);
        captureRunMemoryLocked(session, run);
        publishRunStatusLocked(
                session,
                run,
                previous,
                RunStatus.CANCELLED,
                "协作式取消完成；Worker 保活");
    }

    private void failRunLocked(
            SessionState session,
            RunState run,
            String code,
            String rawMessage) {
        if (run.status.isTerminal()) {
            return;
        }
        if (run.status == RunStatus.CANCELLING) {
            finishRunCancellationLocked(session, run, null);
            return;
        }
        String message = SecretRedactor.redact(rawMessage);
        RunStatus previous = run.status;
        run.status = RunStatus.FAILED;
        run.finishedAt = Instant.now();
        run.pendingApproval = null;
        run.error = mapper.createObjectNode();
        run.error.put("code", code);
        run.error.put("message", message);
        run.summary = run.failureSummary(mapper, session.sessionId, code, message);
        run.outcome = outcomeFromSummary(run.summary);
        publishRunStatusLocked(session, run, previous, RunStatus.FAILED, message);
        captureRunMemoryLocked(session, run);
    }

    private void captureRunMemoryLocked(SessionState session, RunState run) {
        run.runMemory = memoryBuilder.build(
                session.sessionId,
                run,
                history.getRunEvents(session.sessionId, run.runId));
        history.saveRun(session, run);
    }

    private void protocolFailure(SessionState session, String message) {
        synchronized (lock) {
            failSessionLocked(session, "WORKER_PROTOCOL_ERROR", message, null);
        }
    }

    private void workerExited(SessionState session, int exitCode, String stderrTail) {
        synchronized (lock) {
            if (current != session
                    || session.status.isTerminal()
                    || session.status == SessionStatus.SUSPENDED) {
                return;
            }
            if (session.status == SessionStatus.SUSPENDING) {
                finishSessionSuspendLocked(session, false);
                return;
            }
            if (session.status == SessionStatus.CLOSING) {
                // 正常关闭只由 finishSessionCloseLocked 记录一次 worker_exited，
                // 避免进程回调和 terminate 回调各留一条互相矛盾的退出证据。
                finishSessionCloseLocked(session, false);
                return;
            }
            ObjectNode payload = mapper.createObjectNode();
            payload.put("workerId", session.workerId.toString());
            payload.put("exitCode", exitCode);
            payload.put("expected", false);
            publishSessionEventLocked(session, "worker_exited", "WORKER", payload);
            RunState run = session.currentRun;
            if (run != null && run.status == RunStatus.CANCELLING) {
                finishRunCancellationLocked(session, run, null);
            } else if (run != null && run.status.isActive()) {
                failRunLocked(
                        session,
                        run,
                        "WORKER_EXITED",
                        "Worker 在当前 Run 完成前退出。");
            }
            String message = exitCode == 0
                    ? "Worker 意外退出。"
                    : "Worker 异常退出（exit=" + exitCode + "）。";
            failSessionLocked(session, "WORKER_EXITED", message, exitCode);
        }
    }

    private void startTimedOut(SessionState session) {
        synchronized (lock) {
            if (current == session
                    && session.status == SessionStatus.OPENING
                    && !session.workerReady) {
                failSessionLocked(
                        session,
                        "WORKER_START_TIMEOUT",
                        "Worker 未在规定时间内发送 ready。",
                        null);
            }
        }
    }

    private void cancelTimedOut(SessionState session, RunState run) {
        synchronized (lock) {
            if (current != session || run.status != RunStatus.CANCELLING) {
                return;
            }
            finishRunCancellationLocked(session, run, null);
            failSessionLocked(
                    session,
                    "RUN_CANCEL_TIMEOUT",
                    "Worker 未及时确认取消；已关闭该 Session 以避免遗留命令继续运行。",
                    null);
        }
    }

    private void finishSessionClose(SessionState session, boolean forced) {
        synchronized (lock) {
            finishSessionCloseLocked(session, forced);
        }
    }

    private void finishSessionCloseLocked(SessionState session, boolean forced) {
        if (current != session || session.status != SessionStatus.CLOSING) {
            return;
        }
        ObjectNode exited = mapper.createObjectNode();
        exited.put("workerId", session.workerId.toString());
        exited.put("forced", forced);
        exited.put("expected", true);
        publishSessionEventLocked(session, "worker_exited", "WEB", exited);
        closeWorker(session);
        session.closedAt = Instant.now();
        transitionSessionLocked(session, SessionStatus.CLOSED, "Worker 已退出，Session 已关闭");
        completeSubscribersLocked(session);
    }

    private void finishSessionSuspend(SessionState session, boolean forced) {
        synchronized (lock) {
            finishSessionSuspendLocked(session, forced);
        }
    }

    private void finishSessionSuspendLocked(SessionState session, boolean forced) {
        if (current != session || session.status != SessionStatus.SUSPENDING) {
            return;
        }
        ObjectNode exited = mapper.createObjectNode();
        exited.put("workerId", session.workerId.toString());
        exited.put("forced", forced);
        exited.put("expected", true);
        publishSessionEventLocked(session, "worker_exited", "WEB", exited);
        closeWorker(session);
        session.closedAt = null;
        transitionSessionLocked(
                session,
                SessionStatus.SUSPENDED,
                "Worker 已停止；会话可从持久化 Conversation 继续");
        completeSubscribersLocked(session);
    }

    private void failSessionLocked(
            SessionState session,
            String code,
            String rawMessage,
            Integer exitCode) {
        if (current != session || session.status.isTerminal()) {
            return;
        }
        String message = SecretRedactor.redact(
                rawMessage == null ? "Worker 运行失败。" : rawMessage);
        RunState run = session.currentRun;
        if (run != null && run.status.isActive()) {
            failRunLocked(session, run, code, message);
        }
        ObjectNode payload = mapper.createObjectNode();
        payload.put("code", code);
        payload.put("message", message);
        if (exitCode == null) {
            payload.putNull("exitCode");
        } else {
            payload.put("exitCode", exitCode);
        }
        publishSessionEventLocked(session, "worker_error", "WORKER", payload);
        transitionSessionLocked(session, SessionStatus.FAILED, message);
        completeSubscribersLocked(session);
        if (session.worker != null && session.worker.isAlive()) {
            session.worker.terminate(properties.getKillGracePeriod(), ignored -> {});
        }
    }

    private void transitionRunLocked(
            SessionState session,
            RunState run,
            RunStatus next,
            String reason) {
        RunStatus previous = run.status;
        if (previous == next) {
            return;
        }
        if (!previous.canTransitionTo(next)) {
            throw new WorkerProtocolException(
                    "非法 Run 状态迁移：" + previous + " -> " + next);
        }
        run.status = next;
        publishRunStatusLocked(session, run, previous, next, reason);
    }

    private void transitionSessionLocked(
            SessionState session,
            SessionStatus next,
            String reason) {
        SessionStatus previous = session.status;
        if (previous == next) {
            return;
        }
        if (!previous.canTransitionTo(next)) {
            throw new WorkerProtocolException(
                    "非法 Session 状态迁移：" + previous + " -> " + next);
        }
        session.status = next;
        publishSessionStatusLocked(session, previous, next, reason);
    }

    private void publishRunStatusLocked(
            SessionState session,
            RunState run,
            RunStatus previous,
            RunStatus currentStatus,
            String reason) {
        ObjectNode payload = mapper.createObjectNode();
        if (previous == null) {
            payload.putNull("previous");
        } else {
            payload.put("previous", previous.name());
        }
        payload.put("current", currentStatus.name());
        payload.put("reason", reason);
        publishRunEventLocked(session, run, "run_status", "WEB", payload);
    }

    private void publishSessionStatusLocked(
            SessionState session,
            SessionStatus previous,
            SessionStatus currentStatus,
            String reason) {
        ObjectNode payload = mapper.createObjectNode();
        if (previous == null) {
            payload.putNull("previous");
        } else {
            payload.put("previous", previous.name());
        }
        payload.put("current", currentStatus.name());
        payload.put("reason", reason);
        publishSessionEventLocked(session, "session_status", "WEB", payload);
    }

    private void publishRunEventLocked(
            SessionState session,
            RunState run,
            String type,
            String source,
            JsonNode payload) {
        publishLocked(session, run.runId, type, source, payload);
    }

    private void publishSessionEventLocked(
            SessionState session,
            String type,
            String source,
            JsonNode payload) {
        publishLocked(session, null, type, source, payload);
    }

    private void publishLocked(
            SessionState session,
            UUID runId,
            String type,
            String source,
            JsonNode payload) {
        BufferedEvent event = session.appendEvent(
                mapper,
                runId,
                type,
                source,
                payload,
                properties.getEventMaxCount(),
                properties.getEventMaxBytes().toBytes());
        history.saveSession(session);
        if (runId != null) {
            RunState run = session.run(runId);
            if (run != null) {
                history.saveRun(session, run);
            }
        }
        history.saveEvent(event);
        for (SseEmitter subscriber : List.copyOf(session.subscribers)) {
            try {
                sendEvent(subscriber, event);
            } catch (IOException | IllegalStateException exc) {
                session.subscribers.remove(subscriber);
                subscriber.complete();
            }
        }
    }

    private void heartbeat() {
        synchronized (lock) {
            if (current == null || current.subscribers.isEmpty()) {
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

    private void removeSubscriber(SessionState session, SseEmitter emitter) {
        synchronized (lock) {
            session.subscribers.remove(emitter);
        }
    }

    private void completeSubscribersLocked(SessionState session) {
        for (SseEmitter subscriber : List.copyOf(session.subscribers)) {
            subscriber.complete();
        }
        session.subscribers.clear();
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

    private SessionState requireSession(UUID sessionId) {
        if (current == null || !current.sessionId.equals(sessionId)) {
            throw new ApiException(
                    HttpStatus.NOT_FOUND,
                    "SESSION_NOT_FOUND",
                    "Session 不存在。");
        }
        return current;
    }

    private RunState requireRun(SessionState session, UUID runId) {
        RunState run = session.run(runId);
        if (run == null) {
            throw new ApiException(HttpStatus.NOT_FOUND, "RUN_NOT_FOUND", "Run 不存在。");
        }
        return run;
    }

    private RunState requireCurrentRun(SessionState session, UUID runId) {
        RunState run = requireRun(session, runId);
        if (run != session.currentRun) {
            throw new ApiException(
                    HttpStatus.CONFLICT,
                    "RUN_NOT_CURRENT",
                    "该 Run 已不是当前 Run，不能再执行此操作。");
        }
        return run;
    }

    private Path validateWorkspace(String raw) {
        String selected = raw;
        if (selected == null || selected.isBlank()) {
            Path repositoryRoot = properties.getRepositoryRoot().toAbsolutePath().normalize();
            selected = properties.getAllowedRoots().stream()
                    .findFirst()
                    .map(root -> root.isAbsolute()
                            ? root
                            : repositoryRoot.resolve(root).normalize())
                    .orElse(repositoryRoot)
                    .toString();
        }
        final Path candidate;
        try {
            candidate = Path.of(selected.trim());
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

    private List<AttachmentInput> validateAttachments(List<AttachmentInput> raw) {
        List<AttachmentInput> attachments = raw == null ? List.of() : List.copyOf(raw);
        for (AttachmentInput attachment : attachments) {
            String mediaType = attachment.mediaType().trim().toLowerCase();
            if (!mediaType.startsWith("text/") && !TEXT_MEDIA_TYPES.contains(mediaType)) {
                throw new ApiException(
                        HttpStatus.BAD_REQUEST,
                        "UNSUPPORTED_ATTACHMENT",
                        "当前仅支持文本、日志、代码和 JSON/XML/YAML/TOML 附件。",
                        Map.of("field", "attachments"));
            }
        }
        return attachments;
    }

    private void ensureWorkerAlive(SessionState session) {
        if (session.worker == null || !session.worker.isAlive()) {
            throw new ApiException(
                    HttpStatus.CONFLICT,
                    "SESSION_WORKER_UNAVAILABLE",
                    "Session 的 Worker 已退出。");
        }
    }

    private void sendWorkerLocked(
            SessionState session,
            ObjectNode message,
            String errorMessage) {
        ensureWorkerAlive(session);
        try {
            session.worker.send(message);
        } catch (IOException exc) {
            failSessionLocked(session, "WORKER_IO_ERROR", errorMessage, null);
            throw new ApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "INTERNAL_ERROR",
                    errorMessage);
        }
    }

    private ObjectNode workerCommand(String type) {
        ObjectNode message = mapper.createObjectNode();
        message.put("protocolVersion", PROTOCOL_VERSION);
        message.put("type", type);
        message.put("requestId", UUID.randomUUID().toString());
        return message;
    }

    private static void putAttachments(ObjectNode payload, List<AttachmentInput> attachments) {
        ArrayNode values = payload.putArray("attachments");
        for (AttachmentInput attachment : attachments) {
            ObjectNode item = values.addObject();
            item.put("name", attachment.name());
            item.put("mediaType", attachment.mediaType());
            item.put("content", attachment.content());
        }
    }

    private CancelResponse cancellingResponse(SessionState session, RunState run) {
        return new CancelResponse(
                PROTOCOL_VERSION,
                session.sessionId,
                run.runId,
                RunStatus.CANCELLING.name(),
                "正在取消当前 Run；已发生的文件修改不会自动回滚。");
    }

    private CancelResponse cancelledResponse(SessionState session, RunState run) {
        return new CancelResponse(
                PROTOCOL_VERSION,
                session.sessionId,
                run.runId,
                RunStatus.CANCELLED.name(),
                "Run 已取消；Session、Conversation 与已落盘修改均保留。");
    }

    private ObjectNode outcomeFromSummary(ObjectNode summary) {
        ObjectNode outcome = summary.deepCopy();
        outcome.remove(List.of("schemaVersion", "sessionId", "runId", "status", "finishedAt"));
        return outcome;
    }

    private void closeWorker(SessionState session) {
        if (session.worker != null) {
            session.worker.close();
            session.worker = null;
        }
        session.workerPid = null;
        session.workerReady = false;
        session.startSent = false;
    }

    private static Instant parseInstant(String raw, String field) {
        try {
            return Instant.parse(raw);
        } catch (RuntimeException exc) {
            throw new ApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "HISTORY_CORRUPTED",
                    "历史 Session 的 " + field + " 无效。");
        }
    }

    private static String requireText(JsonNode node, String field, String expected) {
        JsonNode value = node.path(field);
        if (!value.isTextual() || value.asText().isBlank()) {
            throw new WorkerProtocolException(field + " 必须是非空字符串。");
        }
        String actual = value.asText();
        if (expected != null && !expected.equals(actual)) {
            throw new WorkerProtocolException(field + " 不匹配。");
        }
        return actual;
    }

    private static ObjectNode requireObject(JsonNode node, String field) {
        JsonNode value = node.path(field);
        if (!value.isObject()) {
            throw new WorkerProtocolException(field + " 必须是对象。");
        }
        return (ObjectNode) value;
    }

    private static int requireInt(JsonNode node, String field) {
        JsonNode value = node.path(field);
        if (!value.isIntegralNumber() || !value.canConvertToInt() || value.intValue() < 0) {
            throw new WorkerProtocolException(field + " 必须是非负整数。");
        }
        return value.intValue();
    }

    private static boolean requireBoolean(JsonNode node, String field) {
        JsonNode value = node.path(field);
        if (!value.isBoolean()) {
            throw new WorkerProtocolException(field + " 必须是布尔值。");
        }
        return value.booleanValue();
    }

    private static UUID parseUuid(String raw, String field) {
        try {
            return UUID.fromString(raw);
        } catch (IllegalArgumentException exc) {
            throw new WorkerProtocolException(field + " 必须是 UUID。");
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
            throw new WorkerProtocolException("期望字符串数组。");
        }
        return java.util.stream.StreamSupport.stream(array.spliterator(), false)
                .map(value -> {
                    if (!value.isTextual()) {
                        throw new WorkerProtocolException("数组元素必须是字符串。");
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
