package dev.hako.web.api;

import dev.hako.web.api.ApiModels.HealthResponse;
import dev.hako.web.service.SessionService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1")
public class HealthController {
    private final SessionService sessions;

    public HealthController(SessionService sessions) {
        this.sessions = sessions;
    }

    @GetMapping("/health")
    public ResponseEntity<HealthResponse> health() {
        return ResponseEntity.ok(sessions.health());
    }
}
