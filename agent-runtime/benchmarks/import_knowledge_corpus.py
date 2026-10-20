"""Import licensed public knowledge through the SQL-backed service, never direct SQL."""

import argparse
import json
import time
from pathlib import Path

import httpx
from capacity import read_env

from psych_agent_runtime.knowledge_sources import licensed_source
from psych_agent_runtime.schemas import KnowledgeUpsertRequest


def run(args):
    corpus = json.loads(Path(args.documents).read_text())
    if corpus.get('license') not in {'public-domain-nimh', 'curated-public-domain-text'}:
        raise ValueError('Reviewed public text corpus required')
    for d in corpus['documents']:
        if not licensed_source({'source': d['source'], **d['metadata']}):
            raise ValueError('Corpus source does not match reviewed license policy')
        KnowledgeUpsertRequest(document_id=d['documentId'], content=d['content'],
                               source=d['source'], category=d['category'], metadata=d['metadata'])
    env = read_env(args.env)
    headers = {'X-Internal-Service-Token': env['INTERNAL_SERVICE_TOKEN']}
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    report = {'corpusVersion': corpus['corpusVersion'], 'sourcePages': len(corpus['sources']),
              'sourceDocuments': len(corpus['documents']), 'runs': []}
    with httpx.Client(headers=headers, timeout=120, trust_env=False) as client:
        for _ in range(40):
            try:
                r = client.get('http://127.0.0.1:18090/health', timeout=2)
                if r.status_code == 200 and r.json().get('graph'): break
            except httpx.HTTPError: pass
            time.sleep(.5)
        else: raise RuntimeError('Runtime not ready; no corpus writes dispatched')
        def knowledge():
            r = client.get('http://127.0.0.1:18090/v1/runtime/info'); r.raise_for_status()
            return r.json()['knowledge']
        report['before'] = knowledge()
        for phase in ['import', 'idempotency_replay']:
            rows = []
            for i, document in enumerate(corpus['documents']):
                r = client.post('http://127.0.0.1:8088/internal/agent/knowledge/upsert', json=document)
                r.raise_for_status(); result = r.json(); rows.append(result)
                (output / (phase + '.json')).write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                if result.get('indexed') is not True:
                    raise RuntimeError('Source saved but indexing pending: ' + document['documentId'])
                if (i + 1) % 25 == 0: print(phase, i + 1, '/', len(corpus['documents']), flush=True)
            report['runs'].append({'phase': phase, 'documentsIndexed': len(rows),
                                   'unchangedSources': sum(r['unchanged'] for r in rows),
                                   'knowledge': knowledge()})
        assert report['runs'][0]['knowledge']['chunks'] == report['runs'][1]['knowledge']['chunks']
        r = client.get('http://127.0.0.1:8088/internal/agent/knowledge'); r.raise_for_status()
        actual = {d['documentId']: d for d in r.json()}
        for d in corpus['documents']:
            assert actual[d['documentId']]['content'] == d['content']
            assert json.loads(actual[d['documentId']]['metadataJson']) == d['metadata']
        report['sqlSourceAndMetadataVerified'] = True
        report['after'] = knowledge()
        (output / 'import-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--env', required=True)
    parser.add_argument('--documents', required=True); parser.add_argument('--output', required=True)
    run(parser.parse_args())
