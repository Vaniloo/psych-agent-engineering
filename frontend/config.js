(() => {
  const pageHost = window.location.hostname;
  const apiHost = pageHost && pageHost !== "0.0.0.0" ? pageHost : "localhost";
  const parameters = new URLSearchParams(window.location.search);
  const configuredApiBaseUrl = parameters.get("apiBaseUrl");
  const developmentPort = Boolean(window.location.port)
    && !["80", "443"].includes(window.location.port);

  window.PSYCH_AGENT_CONFIG = {
    apiBaseUrl: configuredApiBaseUrl
      || (developmentPort ? `http://${apiHost}:8080` : `${window.location.origin}/api`),
    forceApiBaseUrl: Boolean(configuredApiBaseUrl),
    localServices: {
      mysql: "localhost:3306/psych_agent",
      redis: "localhost:6379",
      ollama: "http://localhost:11434",
      chroma: "http://localhost:8000",
    },
  };
})();
