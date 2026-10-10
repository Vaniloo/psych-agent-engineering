package com.vanilo.psych.agent.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.vanilo.psych.agent.exception.ServiceUnavailableException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Service;

/** Server-only credentials; HTTP errors never expose request headers or response bodies. */
@Service
@ConditionalOnProperty(name = "psych.ai.provider", havingValue = "deepseek")
public class DeepSeekLlmService implements LlmService {
  private final ObjectMapper json;
  private final String key, model;
  private final URI endpoint;
  private final HttpClient client =
      HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();

  public DeepSeekLlmService(
      ObjectMapper json,
      @Value("${psych.ai.deepseek.api-key:}") String key,
      @Value("${psych.ai.deepseek.base-url:https://api.deepseek.com}") String baseUrl,
      @Value("${psych.ai.deepseek.model:deepseek-flash}") String model) {
    this.json = json;
    this.key = key;
    this.model = model;
    this.endpoint = URI.create(baseUrl.replaceAll("/+$", "") + "/chat/completions");
    if (key.isBlank()) throw new IllegalArgumentException("DeepSeek API key is required");
  }

  @Override
  public String complete(String systemPrompt, String userPrompt) {
    try {
      var body =
          Map.of(
              "model",
              model,
              "messages",
              List.of(
                  Map.of("role", "system", "content", systemPrompt),
                  Map.of("role", "user", "content", userPrompt)),
              "thinking",
              Map.of("type", "disabled"),
              "stream",
              false,
              "max_tokens",
              2400,
              "temperature",
              0.2);
      var request =
          HttpRequest.newBuilder(endpoint)
              .timeout(Duration.ofSeconds(120))
              .header("Authorization", "Bearer " + key)
              .header("Content-Type", "application/json")
              .POST(HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body)))
              .build();
      var response = client.send(request, HttpResponse.BodyHandlers.ofString());
      if (response.statusCode() != 200)
        throw new ServiceUnavailableException("DeepSeek 调用失败（HTTP " + response.statusCode() + "）");
      var result = json.readTree(response.body());
      var choice = result.path("choices").path(0);
      String content = choice.path("message").path("content").asText("").trim();
      if (content.isBlank() || !"stop".equals(choice.path("finish_reason").asText()))
        throw new ServiceUnavailableException("DeepSeek 未返回完整回复");
      return content;
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      throw new ServiceUnavailableException("AI 调用已中断");
    } catch (ServiceUnavailableException e) {
      throw e;
    } catch (Exception e) {
      throw new ServiceUnavailableException("DeepSeek 服务暂时不可用");
    }
  }

  @Override
  public String provider() {
    return "deepseek";
  }
}
