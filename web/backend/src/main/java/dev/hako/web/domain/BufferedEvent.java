package dev.hako.web.domain;

import tools.jackson.databind.node.ObjectNode;

public record BufferedEvent(
        long eventId,
        String type,
        ObjectNode envelope,
        int serializedBytes,
        boolean critical) {}
