package dev.hako.web.worker;

import tools.jackson.databind.JsonNode;

public interface WorkerListener {
    void onMessage(JsonNode message);

    void onProtocolError(String message);

    void onExit(int exitCode, String stderrTail);
}
