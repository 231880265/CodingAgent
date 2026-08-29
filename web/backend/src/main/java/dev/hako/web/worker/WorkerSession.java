package dev.hako.web.worker;

import tools.jackson.databind.JsonNode;
import java.io.IOException;
import java.time.Duration;
import java.util.function.Consumer;

public interface WorkerSession extends AutoCloseable {
    void send(JsonNode message) throws IOException;

    void terminate(Duration gracePeriod, Consumer<Boolean> completion);

    boolean isAlive();

    @Override
    void close();
}
