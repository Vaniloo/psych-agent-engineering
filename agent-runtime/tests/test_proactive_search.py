from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.web_policy import search_permissions, web_request_for


def settings():
    return replace(Settings(), web_search_enabled=True, web_search_users='alice',
                   web_search_proactive_enabled=True)


@pytest.mark.asyncio
async def test_implicit_external_information_need_grants_search_after_model_judgment():
    model = SimpleNamespace(chat_json=AsyncMock(return_value={
        'topic': 'local_weather', 'search': True, 'query': '广州 今日 天气 降雨',
        'location': '广州', 'locationQuote': '广州今天出门需要带伞吗', 'fresh': True,
        'externalReason': 'freshness', 'publicQuery': True}))
    state = {'username': 'alice', 'message': '广州今天出门需要带伞吗', 'context': {}}
    request = await web_request_for(settings(), state, model)
    assert request['status'] == 'search'
    assert await search_permissions(settings(), state, model, request) == {
        'search_web': {'query': '广州 今日 天气 降雨', 'limit': 5, 'refresh': True}}
    assert model.chat_json.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('message', ['什么是梯度下降', '我最近吃什么', '今天很焦虑', '帮我创建一个学习目标'])
async def test_no_keyword_gate_but_model_can_keep_stable_or_private_tasks_internal(message):
    model = SimpleNamespace(chat_json=AsyncMock(return_value={'topic': 'general', 'search': False}))
    assert await web_request_for(settings(), {'username': 'alice', 'message': message}, model) == {'status': 'none'}
    model.chat_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_search_cannot_send_unconfirmed_private_query_or_override_optout():
    model = SimpleNamespace(chat_json=AsyncMock(return_value={
        'topic': 'general', 'search': True, 'query': '用户私有经历',
        'externalReason': 'source_verification', 'publicQuery': False}))
    state = {'username': 'alice', 'message': '我的事情有进展吗'}
    assert (await web_request_for(settings(), state, model))['status'] == 'none'
    model.chat_json.reset_mock()
    assert await web_request_for(settings(), {**state, 'message': '不要联网，聊聊今天'}, model) == {'status': 'none'}
    model.chat_json.assert_not_awaited()


