package dev.hako.web.domain;

public enum SessionStatus {
    OPENING,
    OPEN,
    CLOSING,
    CLOSED,
    FAILED;

    public boolean isOpen() {
        return this == OPEN;
    }

    public boolean isTerminal() {
        return this == CLOSED || this == FAILED;
    }

    public boolean canTransitionTo(SessionStatus next) {
        return switch (this) {
            case OPENING -> next == OPEN || next == CLOSING || next == FAILED;
            case OPEN -> next == CLOSING || next == FAILED;
            case CLOSING -> next == CLOSED || next == FAILED;
            case CLOSED, FAILED -> false;
        };
    }
}
