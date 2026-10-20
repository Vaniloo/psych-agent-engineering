from types import SimpleNamespace

import pytest

from psych_agent_runtime.config import Settings
from psych_agent_runtime.response import ResponseComposer
from psych_agent_runtime.response_protocol import (
    InternalToolProtocol,
    TextProtocolGuard,
    contains_protocol,
)
from psych_agent_runtime.token_budget import ContextBudgetManager


@pytest.mark.parametrize('protocol', [
    '<｜DSML｜function_calls><｜DSML｜invoke name="search_weather">',
    '< | | DSML | | calls>fake</ | | DSML | | calls>',
    '<tool_call>{"name":"search_weather"}</tool_call>',
    '<function_call name="search_weather">',
    '<invoke name="search_weather">',
])
def test_protocol_is_never_visible_at_any_token_boundary(protocol):
    for boundary in range(len(protocol)+1):
        guard=TextProtocolGuard();visible=[]
        with pytest.raises(InternalToolProtocol):
            for token in [protocol[:boundary],protocol[boundary:]]:
                visible.append(guard.feed(token))
            visible.append(guard.finish())
        assert not ''.join(visible)
    guard=TextProtocolGuard()
    with pytest.raises(InternalToolProtocol):
        for character in protocol:
            assert guard.feed(character)==''


def test_normal_markdown_math_and_html_stream_without_corruption():
    text='广州的资料可看 [来源](https://example.com)。x < 3；<b>普通文字</b>，单独的 <'
    guard=TextProtocolGuard();visible=[]
    for token in text:visible.append(guard.feed(token))
    visible.append(guard.finish())
    assert ''.join(visible)==text and not contains_protocol(text)
    assert contains_protocol('<｜DSML｜calls>')


@pytest.mark.asyncio
async def test_protocol_repairs_once_and_never_executes_model_written_tools():
    events=[];calls=[]
    async def stream(messages,**kwargs):
        calls.append(messages)
        for token in (['<｜DS','ML｜invoke name="search_weather">广州'] if len(calls)==1
                      else ['已查询网页，','暂未核对到可靠实时天气。']):yield token
    composer=ResponseComposer(Settings(),SimpleNamespace(stream_chat=stream),None,lambda n,d:events.append((n,d)))
    stats={};reply=await composer._stream_visible_reply([{'role':'user','content':'广州'}],stats)
    assert len(calls)==2 and stats['responseGuard']=={'protocolBlocked':True,'repaired':True}
    assert reply=='已查询网页，暂未核对到可靠实时天气。'
    assert ''.join(d['text'] for n,d in events if n=='token')==reply
    assert 'DSML' not in reply and 'search_weather' not in reply
    assert not any(n=='tool_activity' for n,d in events)


@pytest.mark.asyncio
async def test_late_protocol_resets_visible_prefix_and_second_failure_is_bounded():
    events=[];attempts=[]
    async def stream(messages,**kwargs):
        attempts.append(messages)
        yield '前面的普通开头'
        yield '<tool_call>{"name":"search_weather"}</tool_call>'
    composer=ResponseComposer(Settings(),SimpleNamespace(stream_chat=stream),None,lambda n,d:events.append((n,d)))
    stats={};reply=await composer._stream_visible_reply([],stats)
    assert len(attempts)==2 and sum(n=='reply_reset' for n,d in events)==2
    assert stats['responseGuard']['repaired'] is False
    visible=''
    for name,payload in events:
        if name=='reply_reset':visible=''
        if name=='token':visible+=payload['text']
    assert visible==reply and 'tool_call' not in visible and '尚未确认' in visible


@pytest.mark.asyncio
async def test_old_assistant_protocol_is_excluded_from_prompt_and_saved_reply():
    prompts=[]
    async def stream(messages,**kwargs):prompts.append(messages);yield '需要先查到来源，才能确认。'
    composer=ResponseComposer(Settings(),SimpleNamespace(stream_chat=stream),ContextBudgetManager(8192,1200),lambda *_:None)
    result=await composer.generate({'username':'alice','session_id':1,'message':'那查到了吗',
        'context':{'username':'alice','sessionId':1,'messages':[
            {'role':'user','content':'广州'},
            {'role':'assistant','content':'<｜DSML｜invoke name="search_weather">'}]},
        'plan':{'intent':'chat'},'tool_results':{}})
    assert result['reply']=='需要先查到来源，才能确认。'
    assert not any(m['role']=='assistant' and 'DSML' in m['content'] for m in prompts[0])
