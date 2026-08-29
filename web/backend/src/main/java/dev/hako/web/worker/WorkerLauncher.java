package dev.hako.web.worker;

import java.io.IOException;

public interface WorkerLauncher {
    WorkerSession launch(WorkerListener listener) throws IOException;
}
