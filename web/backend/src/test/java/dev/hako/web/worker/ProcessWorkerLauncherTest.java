package dev.hako.web.worker;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;

class ProcessWorkerLauncherTest {
    @Test
    void passesGenericHakoModelConfigurationWithoutUnrelatedVariables() {
        var child = new HashMap<String, String>();
        child.put("SHOULD_BE_CLEARED", "old");

        ProcessWorkerLauncher.copyAllowedEnvironment(
                child,
                Map.of(
                        "HAKO_API_KEY", "generic-test-key",
                        "HAKO_BASE_URL", "https://gateway.example/v1",
                        "HAKO_MODEL", "gpt-5.2",
                        "UNRELATED_SECRET", "must-not-pass"));

        assertEquals("generic-test-key", child.get("HAKO_API_KEY"));
        assertEquals("https://gateway.example/v1", child.get("HAKO_BASE_URL"));
        assertEquals("gpt-5.2", child.get("HAKO_MODEL"));
        assertNull(child.get("UNRELATED_SECRET"));
        assertNull(child.get("SHOULD_BE_CLEARED"));
    }

    @Test
    void readsUtf8LineAndStripsCrLf() throws Exception {
        var input = new ByteArrayInputStream("中文消息\r\n".getBytes(StandardCharsets.UTF_8));

        assertEquals("中文消息", ProcessWorkerLauncher.readLimitedLine(input, 64));
        assertNull(ProcessWorkerLauncher.readLimitedLine(input, 64));
    }

    @Test
    void rejectsUnterminatedLine() {
        var input = new ByteArrayInputStream("partial".getBytes(StandardCharsets.UTF_8));

        IOException error = assertThrows(
                IOException.class,
                () -> ProcessWorkerLauncher.readLimitedLine(input, 64));
        assertTrue(error.getMessage().contains("LF"));
    }

    @Test
    void rejectsOversizedLine() {
        var input = new ByteArrayInputStream("12345\n".getBytes(StandardCharsets.UTF_8));

        IOException error = assertThrows(
                IOException.class,
                () -> ProcessWorkerLauncher.readLimitedLine(input, 4));
        assertTrue(error.getMessage().contains("上限"));
    }

    @Test
    void rejectsMalformedUtf8() {
        var input = new ByteArrayInputStream(new byte[] {(byte) 0xC3, 0x28, '\n'});

        assertThrows(
                IOException.class,
                () -> ProcessWorkerLauncher.readLimitedLine(input, 64));
    }
}
