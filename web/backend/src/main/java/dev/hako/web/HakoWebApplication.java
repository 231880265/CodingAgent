package dev.hako.web;

import dev.hako.web.config.WebProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

@SpringBootApplication
@EnableConfigurationProperties(WebProperties.class)
public class HakoWebApplication {
    public static void main(String[] args) {
        SpringApplication.run(HakoWebApplication.class, args);
    }
}
