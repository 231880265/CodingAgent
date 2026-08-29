package dev.hako.web.api;

import tools.jackson.core.JacksonException;
import dev.hako.web.api.ApiModels.ErrorDetail;
import dev.hako.web.api.ApiModels.ErrorEnvelope;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

@RestControllerAdvice
public class GlobalExceptionHandler {
    @ExceptionHandler(ApiException.class)
    public ResponseEntity<ErrorEnvelope> handleApi(ApiException exception) {
        return response(
                exception.status(),
                exception.code(),
                exception.getMessage(),
                exception.details());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorEnvelope> handleValidation(MethodArgumentNotValidException exception) {
        FieldError first = exception.getBindingResult().getFieldErrors().stream().findFirst().orElse(null);
        Map<String, Object> details = first == null ? Map.of() : Map.of("field", first.getField());
        return response(HttpStatus.BAD_REQUEST, "INVALID_REQUEST", "请求字段不符合约束。", details);
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ErrorEnvelope> handleUnreadable(HttpMessageNotReadableException exception) {
        String message = exception.getCause() instanceof JacksonException
                ? "请求 JSON 非法或包含未知字段。"
                : "无法读取请求正文。";
        return response(HttpStatus.BAD_REQUEST, "INVALID_REQUEST", message, Map.of());
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<ErrorEnvelope> handleTypeMismatch(
            MethodArgumentTypeMismatchException exception) {
        return response(
                HttpStatus.BAD_REQUEST,
                "INVALID_REQUEST",
                "路径或查询参数格式不正确。",
                Map.of("field", exception.getName()));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ErrorEnvelope> handleUnexpected(Exception exception) {
        return response(
                HttpStatus.INTERNAL_SERVER_ERROR,
                "INTERNAL_ERROR",
                "后端发生未分类错误。",
                Map.of());
    }

    private ResponseEntity<ErrorEnvelope> response(
            HttpStatus status,
            String code,
            String message,
            Map<String, Object> details) {
        ErrorEnvelope envelope = new ErrorEnvelope(
                "1.0",
                new ErrorDetail(code, message, UUID.randomUUID(), details));
        return ResponseEntity.status(status).body(envelope);
    }
}
