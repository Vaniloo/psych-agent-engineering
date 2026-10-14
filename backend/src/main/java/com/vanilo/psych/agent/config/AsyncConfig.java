package com.vanilo.psych.agent.config;

import org.springframework.context.annotation.Bean;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.Executor;

@Configuration
@EnableAsync
@EnableScheduling
public class AsyncConfig {
    @Bean(name = "alertExecutor")
    public Executor alertExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(2);
        executor.setMaxPoolSize(4);
        executor.setQueueCapacity(200);
        executor.setThreadNamePrefix("risk-alert-");
        executor.initialize();
        return executor;
    }

    @Bean(name = "agentStreamExecutor")
    public Executor agentStreamExecutor(
            @Value("${app.agent.stream.concurrency:${AGENT_STREAM_CONCURRENCY:4}}") int concurrency,
            @Value("${app.agent.stream.queue-capacity:${AGENT_STREAM_QUEUE_CAPACITY:4}}") int queueCapacity) {
        if (concurrency < 1 || queueCapacity < 0) {
            throw new IllegalArgumentException("Invalid Agent stream capacity");
        }
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        // Keep admission aligned with model capacity; a large hidden queue only
        // increases time to first token and lets timed-out tasks keep running.
        executor.setCorePoolSize(concurrency);
        executor.setMaxPoolSize(concurrency);
        executor.setQueueCapacity(queueCapacity);
        executor.setThreadNamePrefix("agent-stream-");
        executor.initialize();
        return executor;
    }
}
