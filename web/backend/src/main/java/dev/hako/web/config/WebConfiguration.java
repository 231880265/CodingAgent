package dev.hako.web.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class WebConfiguration implements WebMvcConfigurer {
    private final WebProperties properties;

    public WebConfiguration(WebProperties properties) {
        this.properties = properties;
    }

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        String origin = properties.getDevAllowedOrigin();
        if (origin != null && !origin.isBlank()) {
            registry.addMapping("/api/**")
                    .allowedOrigins(origin.trim())
                    .allowedMethods("GET", "POST")
                    .allowedHeaders("Accept", "Content-Type", "Last-Event-ID")
                    .allowCredentials(false);
        }
    }
}
