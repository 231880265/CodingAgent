package dev.hako.web.config;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import javax.sql.DataSource;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

@Configuration
public class HistoryConfiguration {
    @Bean
    DataSource historyDataSource(WebProperties properties) throws IOException {
        Path configured = properties.getHistoryDatabase();
        Path database = configured.isAbsolute()
                ? configured.normalize()
                : properties.getRepositoryRoot().toAbsolutePath().normalize().resolve(configured).normalize();
        Path parent = database.getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        DriverManagerDataSource source = new DriverManagerDataSource();
        source.setDriverClassName("org.sqlite.JDBC");
        source.setUrl("jdbc:sqlite:" + database + "?busy_timeout=5000&journal_mode=WAL");
        return source;
    }

    @Bean
    JdbcTemplate historyJdbcTemplate(DataSource historyDataSource) {
        return new JdbcTemplate(historyDataSource);
    }
}
