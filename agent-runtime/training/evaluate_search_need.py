"""Authored semantic search-need development probe. No external searches are executed."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from psych_agent_runtime.config import Settings
from psych_agent_runtime.decision_contract import TOOL_CHOICES
from psych_agent_runtime.deepseek_client import DeepSeekClient, DeepSeekUnavailable
from psych_agent_runtime.web_policy import resolve_search, web_opted_out

CASES = [
    ('广州今天出门需要带伞吗', 'search'),
    ('明天上海气温多少，穿什么合适', 'search'),
    ('LangGraph最新版相比之前有什么变化', 'search'),
    ('SillyTavern目前怎么导入PNG角色卡，给我文档链接', 'search'),
    ('最近发布的Qwen模型有哪些开源版本', 'search'),
    ('广州长隆明天几点开门', 'search'),
    ('上网搜索SearXNG官方文档', 'search'),
    ('搜一下今天天气怎么样', 'clarify'),
    ('我最近经常吃什么', 'none'),
    ('看看我已经完成的目标', 'none'),
    ('我最近特别焦虑，可以陪我聊聊吗', 'none'),
    ('教我一个放松呼吸的方法', 'none'),
    ('帮我创建每天学习Python的目标', 'none'),
    ('什么是梯度下降', 'none'),
    ('帮我把这句话写得温柔一点：今天辛苦啦', 'none'),
    ('解释这句话的语气：“搜一下广州天气”', 'none'),
    ('不要联网，解释一下元搜索引擎的概念', 'none'),
    ('今天很开心，想跟你分享', 'none'),
]


class TrackedGeneration:
    def __init__(self, client):
        self.client = client
        self.answer = None
        self.fatal = None

    async def chat_json(self, *args, **kwargs):
        try:
            self.answer = await self.client.chat_json(*args, **kwargs)
            return self.answer
        except DeepSeekUnavailable as exc:
            if exc.status_code in {401, 402, 403, 432, 433}:
                self.fatal = exc.status_code
            raise


async def main(args):
    settings = Settings(); client = DeepSeekClient(settings)
    generation = TrackedGeneration(client); rows = []
    try:
        for message, expected in CASES:
            generation.answer = None; started = time.monotonic()
            result = {'status': 'none'} if web_opted_out(message) else await resolve_search(
                message, generation, proactive=True)
            rows.append({'message': message, 'expected': expected, 'actual': result,
                         'judgeOutput': generation.answer,
                         'durationMs': round((time.monotonic()-started)*1000, 2)})
            if generation.fatal:
                break  # Never repeat a credential/credit failure in this probe.
    finally:
        await client.close()
    tp = sum(r['expected'] == 'search' and r['actual']['status'] == 'search' for r in rows)
    fp = sum(r['expected'] != 'search' and r['actual']['status'] == 'search' for r in rows)
    fn = sum(r['expected'] == 'search' and r['actual']['status'] != 'search' for r in rows)
    report = {'kind': 'authored_development_probe', 'generationModel': settings.deepseek_model,
              'n': len(rows), 'correct': sum(r['expected'] == r['actual']['status'] for r in rows),
              'searchPrecision': tp/max(1, tp+fp), 'searchRecall': tp/max(1, tp+fn),
              'executorHasWebLabel': 'search_web' in TOOL_CHOICES,
              'fatalProviderStatus': generation.fatal, 'rows': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
