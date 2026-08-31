package dev.hako.web.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import dev.hako.web.api.ApiException;
import dev.hako.web.config.WebProperties;
import dev.hako.web.worker.WorkerLauncher;
import java.util.UUID;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

class SessionServiceDetachedLifecycleTest {
    @ParameterizedTest
    @ValueSource(strings = {"SUSPENDED", "CLOSED", "FAILED"})
    void suspendingPersistedSessionWithoutLiveWorkerIsIdempotent(String status) {
        UUID sessionId = UUID.randomUUID();
        ObjectMapper mapper = new ObjectMapper();
        SessionHistoryRepository history = mock(SessionHistoryRepository.class);
        ObjectNode stored = mapper.createObjectNode().put("status", status);
        when(history.getHistory(sessionId)).thenReturn(stored);
        SessionService service = new SessionService(
                new WebProperties(), mapper, mock(WorkerLauncher.class), history);

        try {
            assertEquals(status, service.suspendSession(sessionId).status());
        } finally {
            service.shutdown();
        }
    }

    @Test
    void suspendingUnknownSessionStillReturnsNotFound() {
        UUID sessionId = UUID.randomUUID();
        ObjectMapper mapper = new ObjectMapper();
        SessionHistoryRepository history = mock(SessionHistoryRepository.class);
        SessionService service = new SessionService(
                new WebProperties(), mapper, mock(WorkerLauncher.class), history);

        try {
            ApiException missing = assertThrows(
                    ApiException.class,
                    () -> service.suspendSession(sessionId));
            assertEquals("SESSION_NOT_FOUND", missing.code());
        } finally {
            service.shutdown();
        }
    }
}
