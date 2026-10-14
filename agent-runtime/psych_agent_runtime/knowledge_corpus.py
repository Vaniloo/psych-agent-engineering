"""Build a versioned, text-only public-domain NIMH corpus from an allowlist."""

import argparse
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

POLICY = "https://www.nimh.nih.gov/site-info/policies"
TOPICS = {
    "anxiety-disorders": ("anxiety", "焦虑 焦虑障碍 担心 害怕 紧张"),
    "depression": ("depression", "抑郁 情绪低落 失去兴趣 心情"),
    "caring-for-your-mental-health": ("self_care", "心理健康 自我照顾 运动 睡眠 放松 支持"),
    "psychotherapies": ("psychotherapy", "心理治疗 心理咨询 认知行为疗法 CBT 咨询师"),
    "attention-deficit-hyperactivity-disorder-adhd": ("adhd", "注意缺陷多动障碍 ADHD 注意力 执行功能"),
    "bipolar-disorder": ("bipolar", "双相障碍 躁狂 心境 情绪波动"),
    "borderline-personality-disorder": ("bpd", "边缘型人格障碍 情绪调节 人际关系"),
    "eating-disorders": ("eating", "进食障碍 厌食 暴食 饮食"),
    "obsessive-compulsive-disorder-ocd": ("ocd", "强迫症 OCD 强迫观念 强迫行为"),
    "schizophrenia": ("psychosis", "精神分裂症 幻觉 妄想 精神病性症状"),
    "post-traumatic-stress-disorder-ptsd": ("trauma", "创伤后应激障碍 PTSD 创伤 闪回"),
    "coping-with-traumatic-events": ("trauma_coping", "应对创伤事件 灾难 恢复 陪伴"),
}
SKIP_SECTIONS = re.compile(
    r"clinical trial|research|statistics|science update|share outreach|additional (?:federal )?resources|"
    r"where can i (?:find|learn)|for more information|reprint|reprodu|publication|medication|brain stimulation",
    re.IGNORECASE,
)
SKIP_BLOCK = re.compile(r"\b988\b|\b911\b|lifeline|hotline|suicide.*(?:call|text)|\b1-\d{3}-\d{3}-\d{4}\b", re.IGNORECASE)


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def text(self):
        return re.sub(r"\s+", " ", " ".join(
            c if isinstance(c, str) else c.text() for c in self.children
        )).strip()


class PageParser(HTMLParser):
    VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, dict(attrs)); self.stack[-1].children.append(n)
        if tag not in self.VOID:
            self.stack.append(n)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]; break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def nodes(node):
    yield node
    for c in node.children:
        if isinstance(c, Node):
            yield from nodes(c)


def blocks(node):
    if node.tag in {"script", "style", "nav", "aside", "footer", "header", "form", "iframe"}:
        return
    if node.tag in {"h1", "h2", "h3", "p", "li"}:
        yield node.tag, node.attrs.get("id", ""), node.text()
        return
    for c in node.children:
        if isinstance(c, Node):
            yield from blocks(c)


def extract_page(html):
    root = PageParser(html).root
    article = next((n for n in nodes(root) if n.tag == "article" and n.attrs.get("id") == "main_content_inner"), None)
    if article is None:
        raise ValueError("Official article container missing; quarantine instead of indexing navigation")
    title, sections, current, parent_excluded = "", [], None, False
    for tag, anchor, text in blocks(article):
        if tag == "h1": title = text
        elif tag in {"h2", "h3"}:
            if tag == "h2": parent_excluded = bool(SKIP_SECTIONS.search(text))
            current = {"heading": text, "anchor": anchor, "paragraphs": [], "excluded": parent_excluded}; sections.append(current)
        elif current and text and not SKIP_BLOCK.search(text) and not re.search(r"Last Reviewed:|Disclaimer|Esta p[aá]gina", text, re.IGNORECASE):
            current["paragraphs"].append(text)
    reviewed = re.search(r"Last Reviewed:\s*([A-Za-z]+\s+\d{4})", root.text())
    kept = [s for s in sections if not s["excluded"] and not SKIP_SECTIONS.search(s["heading"]) and sum(map(len, s["paragraphs"])) >= 80]
    return title, kept, reviewed.group(1) if reviewed else "not-stated"


def allowed_url(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "www.nimh.nih.gov" and parsed.path.startswith("/health/")


def topic_terms(title, category, keywords):
    overrides = [
        (r"post-traumatic stress|PTSD", "trauma", "创伤后应激障碍 PTSD 创伤 闪回"),
        (r"social anxiety", "social_anxiety", "社交焦虑 被评价 害怕社交 演讲紧张"),
        (r"panic disorder", "panic", "惊恐障碍 惊恐发作 心慌 恐慌"),
        (r"generalized anxiety", "gad", "广泛性焦虑 过度担心 GAD"),
        (r"psychosis", "psychosis", "精神病性症状 幻觉 妄想 精神分裂症"),
        (r"children and mental health", "youth", "儿童 青少年 心理健康 家长 情绪 行为"),
        (r"stressed out|stress", "stress", "压力 应激 考试紧张 职场压力 担心"),
        (r"mental health support virtually", "psychotherapy", "线上心理咨询 远程心理支持 线上治疗"),
        (r"my mental health", "self_care", "心理健康 自我照顾 什么时候求助 情绪困扰"),
    ]
    for pattern, cat, terms in overrides:
        if re.search(pattern, title, re.IGNORECASE): return cat, terms
    return category, keywords


def section_terms(heading):
    mappings = [(r"what is|what are.*disorder", "定义 含义 什么是"),
                (r"sign|symptom", "常见表现 症状 识别"), (r"diagnos", "专业评估 诊断"),
                (r"treat|therapy|therapies", "治疗 心理治疗 应对方法"),
                (r"help|support|therapist", "求助 支持 咨询师"),
                (r"cope|coping|care|manage", "自我照顾 应对 调节"),
                (r"cause|risk factor", "影响因素 风险因素")]
    return " ".join(v for pattern, v in mappings if re.search(pattern, heading, re.IGNORECASE))


def build(output, cache, version, max_pages=28):
    output, cache = Path(output), Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    sources, documents, rejected = [], [], []
    with httpx.Client(timeout=45, follow_redirects=True, headers={"User-Agent": "PsychAgent-KnowledgeImporter/1.0"}) as client:
        index = client.get("https://www.nimh.nih.gov/health/topics"); index.raise_for_status()
        links = [n.attrs.get("href", "") for n in nodes(PageParser(index.text).root) if n.tag == "a"]
        seeds = []
        for slug, (category, keywords) in TOPICS.items():
            match = next((urljoin(str(index.url), l) for l in links if l.rstrip("/").endswith("/" + slug)), None)
            if match: seeds.append((match, category, keywords))
            else: rejected.append({"topic": slug, "reason": "topic_not_in_current_official_index"})
        pending, seen = list(seeds), set()
        while pending and len(sources) < max_pages:
            url, category, keywords = pending.pop(0)
            if url in seen: continue
            seen.add(url)
            if not allowed_url(url): raise ValueError("source outside NIMH health allowlist")
            response = client.get(url); response.raise_for_status()
            if not allowed_url(str(response.url)): raise ValueError("redirect outside allowlist")
            raw = response.text
            sha = hashlib.sha256(response.content).hexdigest()
            (cache / (sha + ".html")).write_text(raw)
            title, sections, reviewed = extract_page(raw)
            if re.search(r"Trastorno|Cómo|Qué|Depresión|Español", title, re.IGNORECASE):
                rejected.append({"source": url, "reason": "not_english_scope"}); continue
            category, keywords = topic_terms(title, category, keywords)
            # Listing pages are discovery only. Never index their navigation.
            if "/topics/" in url or url.endswith("-listing"):
                for n in nodes(PageParser(raw).root):
                    link = urljoin(url, n.attrs.get("href", "")) if n.tag == "a" else ""
                    if (allowed_url(link) and "/health/publications/" in link and not link.endswith(".pdf")
                            and any(w in link for w in ["anxiety", "depression", "panic", "social", "stress", "obsessive", "bipolar", "borderline", "eating", "psychosis", "traumatic", "adhd"])):
                        pending.append((link, category, keywords))
            if not sections: rejected.append({"source": url, "reason": "no_eligible_sections"}); continue
            sources.append({"source": str(response.url), "title": title, "sourceSha256": sha,
                            "sourceReviewedAt": reviewed, "license": "public-domain-nimh"})
            for section in sections:
                text = "\n".join(section["paragraphs"])
                if len(text) > 28000: rejected.append({"source": url, "section": section["heading"], "reason": "section_too_large"}); continue
                identity = str(response.url).split("#")[0] + "|" + section["heading"]
                did = "corpus-nimh-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
                metadata = {"title": keywords.split()[0] + " / " + title, "keywords": keywords,
                            "section": (section["heading"] + " / " + section_terms(section["heading"]))[:500], "language": "en-with-zh-index-terms",
                            "license": "public-domain-nimh", "corpusVersion": version,
                            "reviewedAt": time.strftime("%Y-%m-%d"), "sourceReviewedAt": reviewed,
                            "contentSha256": hashlib.sha256(text.encode()).hexdigest(), "sourceSha256": sha}
                documents.append({"documentId": did, "content": text, "category": category,
                                  "source": str(response.url), "metadata": metadata})
    unique, content_seen = {}, set()
    for d in documents:
        digest = d["metadata"]["contentSha256"]
        if digest in content_seen:
            rejected.append({"source": d["source"], "reason": "exact_duplicate_text", "documentId": d["documentId"]})
            continue
        content_seen.add(digest); unique[d["documentId"]] = d
    payload = {"corpusVersion": version, "publisher": "National Institute of Mental Health (NIMH)",
               "license": "public-domain-nimh", "licenseSource": POLICY,
               "scope": "General mental-health education; no diagnosis or individualized treatment; no images or local hotline numbers",
               "sources": sources, "documents": list(unique.values()), "rejected": rejected}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"sourcePages": len(sources), "documents": len(unique), "excluded": len(rejected)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True); parser.add_argument("--cache", required=True)
    parser.add_argument("--version", default="nimh-20261014-v1"); parser.add_argument("--max-pages", type=int, default=28)
    args = parser.parse_args(); build(args.output, args.cache, args.version, args.max_pages)
