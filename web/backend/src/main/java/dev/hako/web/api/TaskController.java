package dev.hako.web.api;

import dev.hako.web.api.ApiModels.ApprovalDecisionRequest;
import dev.hako.web.api.ApiModels.ApprovalResponse;
import dev.hako.web.api.ApiModels.CancelResponse;
import dev.hako.web.api.ApiModels.CreateTaskRequest;
import dev.hako.web.service.TaskService;
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
@RequestMapping("/api/v1/tasks")
public class TaskController {
    private final TaskService tasks;

    public TaskController(TaskService tasks) {
        this.tasks = tasks;
    }

    @PostMapping
    public ResponseEntity<?> create(@Valid @RequestBody CreateTaskRequest request) {
        return ResponseEntity.accepted().body(tasks.createTask(request));
    }

    @GetMapping("/{taskId}")
    public ResponseEntity<?> get(@PathVariable UUID taskId) {
        return ResponseEntity.ok(tasks.getTask(taskId));
    }

    @GetMapping(value = "/{taskId}/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public ResponseEntity<SseEmitter> events(
            @PathVariable UUID taskId,
            @RequestHeader(value = "Last-Event-ID", required = false) String lastEventId) {
        Long parsed = parseEventId(lastEventId);
        return ResponseEntity.ok()
                .header(HttpHeaders.CACHE_CONTROL, "no-cache, no-transform")
                .header(HttpHeaders.CONNECTION, "keep-alive")
                .header("X-Accel-Buffering", "no")
                .body(tasks.subscribe(taskId, parsed));
    }

    @PostMapping("/{taskId}/approvals/{approvalId}")
    public ResponseEntity<ApprovalResponse> approve(
            @PathVariable UUID taskId,
            @PathVariable UUID approvalId,
            @Valid @RequestBody ApprovalDecisionRequest request) {
        return ResponseEntity.accepted()
                .body(tasks.respondApproval(taskId, approvalId, request.decision()));
    }

    @PostMapping("/{taskId}/cancel")
    public ResponseEntity<CancelResponse> cancel(@PathVariable UUID taskId) {
        CancelResponse result = tasks.cancelTask(taskId);
        HttpStatus status = "CANCELLED".equals(result.status()) ? HttpStatus.OK : HttpStatus.ACCEPTED;
        return ResponseEntity.status(status).body(result);
    }

    @GetMapping("/{taskId}/summary")
    public ResponseEntity<?> summary(@PathVariable UUID taskId) {
        return ResponseEntity.ok(tasks.getSummary(taskId));
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
