package com.vanilo.psych.agent.service;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import com.vanilo.psych.agent.exception.ServiceUnavailableException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;

class DeepSeekLlmServiceTests {
  @Test
  void completeUsesServerCredentialAndRejectsTruncatedResponses() throws Exception {
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    var response =
        new AtomicReference<>(
            "{\"choices\":[{\"finish_reason\":\"stop\",\"message\":{\"content\":\"生成成功\"}}]}");
    var input = new AtomicReference<String>();
    server.createContext(
        "/chat/completions",
        exchange -> {
          assertEquals(
              "Bearer test-private-key", exchange.getRequestHeaders().getFirst("Authorization"));
          input.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
          var bytes = response.get().getBytes(StandardCharsets.UTF_8);
          exchange.sendResponseHeaders(200, bytes.length);
          exchange.getResponseBody().write(bytes);
          exchange.close();
        });
    server.start();
    try {
      var llm =
          new DeepSeekLlmService(
              new ObjectMapper(),
              "test-private-key",
              "http://127.0.0.1:" + server.getAddress().getPort(),
              "deepseek-flash");
      assertEquals("生成成功", llm.complete("system", "user"));
      assertTrue(input.get().contains("deepseek-flash"));
      assertFalse(input.get().contains("test-private-key"));
      response.set(
          "{\"choices\":[{\"finish_reason\":\"length\",\"message\":{\"content\":\"未完\"}}]}");
      assertThrows(ServiceUnavailableException.class, () -> llm.complete("system", "user"));
    } finally {
      server.stop(0);
    }
  }

  @Test
  void errorDoesNotExposeRemoteBody() throws Exception {
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext(
        "/chat/completions",
        exchange -> {
          var bytes = "sensitive remote body".getBytes(StandardCharsets.UTF_8);
          exchange.sendResponseHeaders(401, bytes.length);
          exchange.getResponseBody().write(bytes);
          exchange.close();
        });
    server.start();
    try {
      var llm =
          new DeepSeekLlmService(
              new ObjectMapper(),
              "test",
              "http://127.0.0.1:" + server.getAddress().getPort(),
              "deepseek-flash");
      var error =
          assertThrows(ServiceUnavailableException.class, () -> llm.complete("system", "user"));
      assertTrue(error.getMessage().contains("401"));
      assertFalse(error.getMessage().contains("sensitive"));
    } finally {
      server.stop(0);
    }
  }
}
