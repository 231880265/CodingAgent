package dev.hako.web.api;

import dev.hako.web.api.ApiModels.ApprovalDecisionRequest;
import dev.hako.web.api.ApiModels.ApprovalResponse;
import dev.hako.web.api.ApiModels.CancelResponse;
import dev.hako.web.api.ApiModels.CreateRunRequest;
import dev.hako.web.api.ApiModels.CreateSessionRequest;
import dev.hako.web.api.ApiModels.SessionCloseResponse;
import dev.hako.web.service.SessionService;
import jakarta.validation.Valid;
import java.util.UUID;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
@RequestMapping("/api/v1/sessions")
public class SessionController {
    private final SessionService sessions;

    public SessionController(SessionService sessions) {
        this.sessions = sessions;
    }

    @PostMapping
    public ResponseEntity<?> create(@Valid @RequestBody CreateSessionRequest request) {
        return ResponseEntity.accepted().body(sessions.createSession(request));
    }

    @GetMapping
    public ResponseEntity<?> listHistory() {
        return ResponseEntity.ok(sessions.listHistory());
    }

    @GetMapping("/{sessionId}")
    public ResponseEntity<?> get(@PathVariable UUID sessionId) {
        return ResponseEntity.ok(sessions.getSession(sessionId));
    }

    @GetMapping("/{sessionId}/history")
    public ResponseEntity<?> history(@PathVariable UUID sessionId) {
        return ResponseEntity.ok(sessions.getHistory(sessionId));
    }

    @PostMapping("/{sessionId}/runs")
    public ResponseEntity<?> createRun(
            @PathVariable UUID sessionId,
            @Valid @RequestBody CreateRunRequest request) {
        return ResponseEntity.accepted().body(sessions.createRun(sessionId, request));
    }

    @GetMapping(value = "/{sessionId}/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public ResponseEntity<SseEmitter> events(
            @PathVariable UUID sessionId,
            @RequestHeader(value = "Last-Event-ID", required = false) String lastEventId) {
        Long parsed = parseEventId(lastEventId);
        return ResponseEntity.ok()
                .header(HttpHeaders.CACHE_CONTROL, "no-cache, no-transform")
                .header(HttpHeaders.CONNECTION, "keep-alive")
                .header("X-Accel-Buffering", "no")
                .body(sessions.subscribe(sessionId, parsed));
    }

    @PostMapping("/{sessionId}/runs/{runId}/approvals/{approvalId}")
    public ResponseEntity<ApprovalResponse> approve(
            @PathVariable UUID sessionId,
            @PathVariable UUID runId,
            @PathVariable UUID approvalId,
            @Valid @RequestBody ApprovalDecisionRequest request) {
        return ResponseEntity.accepted()
                .body(sessions.respondApproval(
                        sessionId,
                        runId,
                        approvalId,
                        request.decision()));
    }

    @PostMapping("/{sessionId}/runs/{runId}/cancel")
    public ResponseEntity<CancelResponse> cancel(
            @PathVariable UUID sessionId,
            @PathVariable UUID runId) {
        CancelResponse result = sessions.cancelRun(sessionId, runId);
        HttpStatus status = "CANCELLED".equals(result.status()) ? HttpStatus.OK : HttpStatus.ACCEPTED;
        return ResponseEntity.status(status).body(result);
    }

    @GetMapping("/{sessionId}/runs/{runId}/summary")
    public ResponseEntity<?> summary(
            @PathVariable UUID sessionId,
            @PathVariable UUID runId) {
        return ResponseEntity.ok(sessions.getRunSummary(sessionId, runId));
    }

    @PostMapping("/{sessionId}/close")
    public ResponseEntity<SessionCloseResponse> close(@PathVariable UUID sessionId) {
        SessionCloseResponse result = sessions.closeSession(sessionId);
        HttpStatus status = "CLOSING".equals(result.status())
                ? HttpStatus.ACCEPTED
                : HttpStatus.OK;
        return ResponseEntity.status(status).body(result);
    }

    private static Long parseEventId(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        try {
            return Long.parseLong(raw.trim());
        } catch (NumberFormatException exc) {
            throw new ApiException(
                    HttpStatus.BAD_REQUEST,
                    "INVALID_REQUEST",
                    "Last-Event-ID 必须是整数。") ;
        }
    }
}
