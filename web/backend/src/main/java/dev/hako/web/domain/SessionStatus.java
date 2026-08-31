package dev.hako.web.domain;

public enum SessionStatus {
    OPENING,
    OPEN,
    SUSPENDING,
    SUSPENDED,
    CLOSING,
    CLOSED,
    FAILED;

    public boolean isOpen() {
        return this == OPEN;
    }

    public boolean isTerminal() {
        return this == CLOSED || this == FAILED;
    }

    public boolean isDetached() {
        return this == SUSPENDED || isTerminal();
    }

    public boolean canTransitionTo(SessionStatus next) {
        return switch (this) {
            case OPENING -> next == OPEN
                    || next == SUSPENDING
                    || next == CLOSING
                    || next == FAILED;
            case OPEN -> next == SUSPENDING || next == CLOSING || next == FAILED;
            case SUSPENDING -> next == SUSPENDED || next == FAILED;
            case SUSPENDED -> next == OPENING || next == CLOSED || next == FAILED;
            case CLOSING -> next == CLOSED || next == FAILED;
            case CLOSED, FAILED -> false;
        };
    }
}
