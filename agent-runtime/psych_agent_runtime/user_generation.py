"""Request-scoped compatible generation; never place credentials in graph state."""

import asyncio
import ipaddress
import socket
from contextlib import asynccontextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

active_generation = ContextVar("user_generation", default=None)


class ModelApiConfig(BaseModel):
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=160)
    api_key: str = Field(alias="apiKey", min_length=1, max_length=4096, repr=False, exclude=True)
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @field_validator("base_url")
    @classmethod
    def public_endpoint(cls, value):
        value = value.strip().rstrip("/")
        p = urlsplit(value)
        if (p.scheme != "https" or not p.hostname or p.username or p.password
                or p.query or p.fragment or p.port not in {None, 443}
                or any(c.isspace() for c in value) or "%" in p.netloc):
            raise ValueError("需要公开的HTTPS接口地址，不包含密钥、查询参数或端口")
        if p.path.endswith("/chat/completions"):
            value = value[:-len("/chat/completions")]
        return value

    @field_validator("model", "api_key")
    @classmethod
    def clean_text(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("字段不能为空或包含控制字符")
        return value


class PublicEndpointTransport(httpx.AsyncBaseTransport):
    """Pin each request to a validated public IP; retain TLS hostname checking."""

    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        addresses = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
            request.url.host, 443, type=socket.SOCK_STREAM), timeout=5)
        ips = {row[4][0] for row in addresses}
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError("模型API地址必须解析到公开网络")
        host = request.url.host
        request.url = request.url.copy_with(host=min(ips))
        request.headers["Host"] = host
        request.extensions["sni_hostname"] = host
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


@asynccontextmanager
async def generation_for_user(backend, username):
    fetch = getattr(backend, "model_api", None)
    config = await fetch(username) if callable(fetch) else {}
    if not isinstance(config, dict) or not config.get("enabled"):
        yield
        return
    from .deepseek_client import DeepSeekClient
    try:
        parsed = ModelApiConfig.model_validate(config)
    except ValueError:
        raise RuntimeError("已保存的模型API配置无效，请重新保存") from None
    client = DeepSeekClient(None, custom=parsed)
    token = active_generation.set(client)
    from .telemetry import current_run
    run = current_run()
    if run is not None:
        run.custom_generation_model = client.model
    try:
        yield
    finally:
        active_generation.reset(token)
        await client.close()


async def test_connection(config):
    from .deepseek_client import DeepSeekClient, DeepSeekUnavailable
    client = DeepSeekClient(None, custom=config)
    client.max_tokens = 16
    try:
        await asyncio.wait_for(client.chat([
            {"role": "user", "content": "Reply with OK only."}], purpose="connection_test"), timeout=20)
        return {"ok": True, "message": "连接成功，模型可以正常回复"}
    except DeepSeekUnavailable as exc:
        message = {401: "API Key 无效或已过期", 403: "当前密钥没有访问权限", 404: "接口地址或模型名不存在",
                   429: "服务限流或额度不足"}.get(exc.status_code, "模型服务暂时无法完成请求")
        return {"ok": False, "message": message}
    except (TimeoutError, httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, RuntimeError):
        return {"ok": False, "message": "连接未通过，请检查HTTPS地址、模型名和服务可用性"}
    finally:
        await client.close()
