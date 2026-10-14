"""Verify existing knowledge and idempotently index a reviewed public starter.

Never delete old knowledge or copy evaluation collections into the live corpus.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capacity import read_env


def main(args):
    env = read_env(args.env)
    headers = {"X-Internal-Service-Token": env["INTERNAL_SERVICE_TOKEN"]}
    documents = json.loads(Path(args.documents).read_text())["documents"]
    with httpx.Client(
        base_url="http://127.0.0.1:18090", headers=headers, timeout=90, trust_env=False
    ) as client:
        for _ in range(40):
            try:
                response = client.get("/health", timeout=2)
                if response.status_code == 200 and response.json().get("graph"):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("Runtime not ready; knowledge unchanged")
        before = client.get("/v1/runtime/info")
        before.raise_for_status()
        added = []
        for document in documents:
            payload = {k: document[k] for k in ("document_id", "category", "source", "content")}
            if not payload["document_id"].startswith("guidance-") or not payload[
                "source"
            ].startswith("https://www.nhs.uk/"):
                raise ValueError("Only the reviewed public starter is authorized by this probe")
            response = client.post("/v1/knowledge/upsert", json=payload)
            response.raise_for_status()
            added.append(response.json())
        searched = client.post(
            "/v1/knowledge/search", json={"query": "什么是认知行为疗法？", "limit": 6}
        )
        searched.raise_for_status()
        after = client.get("/v1/runtime/info")
        after.raise_for_status()
        result = {
            "before": before.json().get("knowledge"),
            "after": after.json().get("knowledge"),
            "indexed": added,
            "search": searched.json(),
            "originalKnowledgeDeleted": False,
        }
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {"before": result["before"], "after": result["after"], "indexed": added},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True)
    parser.add_argument("--documents", required=True)
    parser.add_argument("--out", required=True)
    main(parser.parse_args())
