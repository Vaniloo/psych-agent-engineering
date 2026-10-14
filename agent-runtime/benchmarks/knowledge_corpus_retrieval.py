import argparse
import json
import time
from pathlib import Path

import httpx
from capacity import read_env


def run(args):
    env = read_env(args.env)
    cases = json.loads(Path(args.cases).read_text())['cases']
    rows = []
    with httpx.Client(headers={'X-Internal-Service-Token': env['INTERNAL_SERVICE_TOKEN']}, timeout=90, trust_env=False) as client:
        for _ in range(40):
            try:
                r = client.get('http://127.0.0.1:18090/health', timeout=2)
                if r.status_code == 200 and r.json().get('graph'): break
            except httpx.HTTPError: pass
            time.sleep(.5)
        else: raise RuntimeError('Runtime not ready; no retrieval requests dispatched')
        for case in cases:
            response = client.post('http://127.0.0.1:18090/v1/knowledge/search', json={'query': case['query'], 'limit': 6})
            response.raise_for_status(); payload = response.json()
            hits = payload['results']
            def matches(hit, expected=tuple(case['expectedSourceContains'])):
                return any(s in hit.get('source', '') for s in expected)
            row = {**case, 'hitAt3': any(map(matches, hits[:3])), 'hitAt6': any(map(matches, hits)),
                   'hits': hits, 'metrics': payload.get('metrics')}
            rows.append(row)
            print(case['query'], 'top3', row['hitAt3'], 'top6', row['hitAt6'], flush=True)
    result = {'evaluationScope': 'developer-defined retrieval diagnostic, not independent clinical validation',
              'cases': len(rows), 'hitAt3': sum(r['hitAt3'] for r in rows), 'hitAt6': sum(r['hitAt6'] for r in rows),
              'rows': rows}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in result.items() if k != 'rows'}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--env', required=True)
    parser.add_argument('--cases', required=True); parser.add_argument('--output', required=True)
    run(parser.parse_args())
