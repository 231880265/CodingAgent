package dev.hako.web.worker;

import java.util.regex.Pattern;

public final class SecretRedactor {
    private static final Pattern API_KEY = Pattern.compile(
            "(?i)(api[_-]?key\\s*[=:]\\s*)[^\\s,;]+");
    private static final Pattern SK_TOKEN = Pattern.compile(
            "\\bsk-[A-Za-z0-9_-]{12,}\\b");
    private static final Pattern BEARER = Pattern.compile(
            "(?i)(authorization\\s*:\\s*bearer\\s+)[^\\s,;]+");

    private SecretRedactor() {}

    public static String redact(String value) {
        if (value == null || value.isEmpty()) {
            return "";
        }
        String redacted = API_KEY.matcher(value).replaceAll("$1[REDACTED]");
        redacted = SK_TOKEN.matcher(redacted).replaceAll("[REDACTED]");
        return BEARER.matcher(redacted).replaceAll("$1[REDACTED]");
    }
}
