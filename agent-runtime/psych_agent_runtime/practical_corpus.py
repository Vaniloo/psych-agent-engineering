"""Curated agency text snapshots, with layout checks and explicit provenance."""

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .knowledge_corpus import PageParser, nodes
from .knowledge_sources import source_policy

EXCLUDED = re.compile(
    r"research|clinical trials?|references|acknowledg|more information|resources|reprints?|reproduction|"
    r"for more information|find out more|share.*(?:social|infographic)|"
    r"(?:medicines|medications|prescription|supplements)|brochure|content source|"
    r"about this page|sources?$|related (?:pages|links)|newsletter|download|^where can i|^what is nccih",
    re.IGNORECASE,
)
REGIONAL = re.compile(r"\b(?:988|911)\b|lifeline|hotline|\b1[- ]\d{3}[- ]\d{3}[- ]\d{4}\b", re.IGNORECASE)


def clean_text(node):
    if node.tag in {"style", "script", "noscript"}:
        return ""
    return re.sub(r"\s+", " ", " ".join(
        child if isinstance(child, str) else clean_text(child) for child in node.children)).strip()


def scoped_blocks(node):
    if (node.tag in {"script", "style", "nav", "aside", "footer", "header", "form", "iframe", "figure"}
            or node.attrs.get("id", "").startswith("content-sources")
            or any(part in node.attrs.get("class", "") for part in (
                "breadcrumb", "book-navigation", "block-language", "visually-hidden", "subnav-menu"))):
        return
    if node.tag in {"h1", "h2", "h3", "h4", "p", "li"}:
        yield node.tag, clean_text(node)
        return
    for child in node.children:
        if not isinstance(child, str):
            yield from scoped_blocks(child)


def extract(html, url, excluded=""):
    policy = source_policy(url)
    if not policy:
        raise ValueError("unreviewed source")
    root = PageParser(html).root
    candidates = list(nodes(root))
    if policy["id"] == "nimh":
        article = next((n for n in candidates if n.tag == "article" and n.attrs.get("id") == "main_content_inner"), None)
    elif policy["id"] == "nhlbi":
        article = next((n for n in candidates if n.tag == "article" and "node--type-health-topics" in n.attrs.get("class", "")), None)
    elif policy["id"] == "cdc":
        article = next((n for n in candidates if n.attrs.get("id") == "content" and "cdc-page-content" in n.attrs.get("class", "")), None)
    else:
        article = next((n for n in candidates if n.tag == "main"), None)
    if article is None:
        raise ValueError("article layout changed; quarantine source")
    title = next((clean_text(n) for n in candidates if n.tag == "h1"), "")
    if not title:
        raise ValueError("missing source title")
    extra = re.compile(excluded, re.IGNORECASE) if excluded else None
    sections = []; current = {"heading": title, "paragraphs": [], "excluded": False}
    sections.append(current); excluded_at = None
    for tag, text in scoped_blocks(article):
        if tag in {"h1", "h2", "h3", "h4"}:
            level = int(tag[1])
            if excluded_at is not None and level <= excluded_at:
                excluded_at = None
            skip = bool(EXCLUDED.search(text) or extra and extra.search(text))
            if skip and excluded_at is None:
                excluded_at = level
            current = {"heading": text, "paragraphs": [], "excluded": excluded_at is not None}
            sections.append(current)
        elif (text and not current["excluded"] and not REGIONAL.search(text)
              and not re.search(r"^Last (?:updated|reviewed|checked)|^Revised \d|^Image:|^Source:", text, re.IGNORECASE)):
            current["paragraphs"].append(text)
    kept = [{"heading": s["heading"], "text": "\n".join(dict.fromkeys(s["paragraphs"]))}
            for s in sections if not s["excluded"] and sum(map(len, s["paragraphs"])) >= 80]
    if not kept:
        raise ValueError("no eligible source sections")
    # Prefer source article metadata; never call a site's footer date a review.
    declared = next((n.attrs.get("content", "") for n in candidates if n.tag == "meta"
                     and n.attrs.get("property", n.attrs.get("name", "")) in {
                         "article:modified_time", "dcterms.modified", "cdc:last_updated"}), "not-stated")
    return title, kept, declared


def build(manifest, output, cache, existing=None):
    spec = json.loads(Path(manifest).read_text())
    output, cache = Path(output), Path(cache)
    if output.exists():
        raise FileExistsError("refusing to overwrite a corpus snapshot")
    cache.mkdir(parents=True, exist_ok=True)
    documents, sources, rejected, content_seen, identities = [], [], [], set(), set()
    if existing:
        for doc in json.loads(Path(existing).read_text())["documents"]:
            content_seen.add(hashlib.sha256(doc["content"].encode()).hexdigest())
            identities.add(doc["documentId"])
    with httpx.Client(timeout=35, follow_redirects=False, headers={"User-Agent": "PsychAgent-KnowledgeImporter/2.0"}) as client:
        for source in spec["sources"]:
            url = source["url"]; policy = source_policy(url)
            if not policy:
                raise ValueError("manifest source not reviewed")
            path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".html")
            try:
                if path.exists():
                    raw = path.read_text()
                else:
                    response = client.get(url)
                    # Only follow an in-policy redirect, before issuing the next request.
                    for _ in range(3):
                        if not response.is_redirect:
                            break
                        target = str(response.url.join(response.headers["location"]))
                        if not source_policy(target):
                            raise ValueError("redirect outside reviewed source paths")
                        response = client.get(target)
                    response.raise_for_status()
                    if len(response.content) > 2_000_000:
                        raise ValueError("source page exceeds byte budget")
                    raw = response.text; path.write_text(raw)
                title, sections, declared = extract(raw, url, source.get("excludeHeadings", ""))
            except (ValueError, httpx.HTTPError) as exc:
                rejected.append({"source": url, "reason": type(exc).__name__,
                                 "detail": str(exc) if isinstance(exc, ValueError) else str(exc.response.status_code)
                                 if isinstance(exc, httpx.HTTPStatusError) else "source fetch failed"})
                continue
            sha = hashlib.sha256(raw.encode()).hexdigest()
            sources.append({"source": url, "title": title, "publisher": policy["publisher"],
                            "license": policy["license"], "licenseSource": policy["policy"],
                            "sourceSha256": sha, "sourceReviewedAt": declared})
            for section in sections:
                text = section["text"]; digest = hashlib.sha256(text.encode()).hexdigest()
                did = "corpus-" + policy["id"] + "-" + hashlib.sha256((url + "|" + section["heading"]).encode()).hexdigest()[:24]
                if digest in content_seen or did in identities or len(text) > 18000:
                    rejected.append({"source": url, "section": section["heading"], "reason": "duplicate_or_oversize"})
                    continue
                content_seen.add(digest); identities.add(did)
                metadata = {"title": source["keywords"].split()[0] + " / " + title,
                    "keywords": source["keywords"], "section": section["heading"][:500],
                    "language": "en-with-zh-index-terms", "license": policy["license"],
                    "corpusVersion": spec["corpusVersion"], "reviewedAt": str(datetime.now(UTC).date()),
                    "sourceReviewedAt": declared[:500], "contentSha256": digest, "sourceSha256": sha,
                    "publisher": policy["publisher"], "licenseSource": policy["policy"],
                    "audience": source["audience"], "contentKind": "public-health-education"}
                documents.append({"documentId": did, "content": text, "source": url,
                                  "category": source["category"], "metadata": metadata})
            print(json.dumps({"source": urlparse(url).path, "sections": len(sections)}, ensure_ascii=False), flush=True)
    payload = {"corpusVersion": spec["corpusVersion"], "license": "curated-public-domain-text",
               "scope": spec["scope"], "sources": sources, "documents": documents, "rejected": rejected}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"sourcePages": len(sources), "documents": len(documents), "excluded": len(rejected)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True); parser.add_argument("--output", required=True)
    parser.add_argument("--cache", required=True); parser.add_argument("--existing")
    args = parser.parse_args(); build(args.manifest, args.output, args.cache, args.existing)
