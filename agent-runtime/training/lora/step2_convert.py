"""第 2 步：把 ToolACE 原始记录转换成统一的“对话事件”。

原始记录：
    {"system": "...Here is a list of functions in JSON format that you can invoke:\\n[{...工具定义...}]...",
     "conversations": [{"from": "user", "value": "..."},
                       {"from": "assistant", "value": "[Get Weather(city='Paris')]"},   # 工具调用
                       {"from": "tool", "value": "[{...工具返回...}]"},
                       {"from": "assistant", "value": "Paris is sunny today."}]}       # 文字回复

转换后（一行一个 episode）：
    {"id": "...", "tools": [...工具定义...],
     "events": [{"role": "user", "content": "..."},
                {"role": "tool_call", "content": "[Get Weather(city='Paris')]",
                 "calls": [{"name": "Get Weather", "arguments": {"city": "Paris"}}]},
                {"role": "observation", "content": "..."},
                {"role": "assistant", "content": "Paris is sunny today."}]}

助手消息以 "[" 开头就当作工具调用解析。解析失败的整条记录丢掉，不猜测，
否则一次失败的工具调用会被误当成“文字回复”，变成错误标签。

    python -m training.lora.step2_convert --raw work/raw/data.json --out work/episodes.jsonl
"""

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .common import write_jsonl

ANCHOR = "Here is a list of functions in JSON format that you can invoke:"
ROLES = {"user": "user", "assistant": "assistant", "tool": "observation"}


class SkipRecord(ValueError):
    """这条记录不能可靠转换，整条跳过。"""


def parse_catalog(system: str) -> list[dict]:
    """system 提示里锚点后面紧跟一个 JSON 数组，就是这条对话可用的工具列表。"""
    if ANCHOR not in system:
        raise SkipRecord("no_tool_catalog")
    tools, _ = json.JSONDecoder().raw_decode(system.split(ANCHOR, 1)[1].lstrip())
    if not isinstance(tools, list) or not all(isinstance(t, dict) and isinstance(t.get("name"), str)
                                              and isinstance(t.get("parameters"), dict) for t in tools):
        raise SkipRecord("invalid_tool_catalog")
    if len({t["name"] for t in tools}) != len(tools):
        raise SkipRecord("duplicate_tool_names")
    return tools


def _literal(node):
    """把一个参数值的语法树转成 JSON 值。只认字面量，绝不执行代码。"""
    if isinstance(node, ast.Name) and node.id in {"true", "false", "null"}:  # 源数据里混用了 JSON 写法
        return {"true": True, "false": False, "null": None}[node.id]
    if isinstance(node, ast.List):
        return [_literal(v) for v in node.elts]
    if isinstance(node, ast.Dict):
        if any(k is None for k in node.keys):  # {**x} 这种解包
            raise SkipRecord("unpacking_in_arguments")
        keys = [_literal(k) for k in node.keys]
        if not all(isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
            raise SkipRecord("non_json_object")
        return dict(zip(keys, (_literal(v) for v in node.values), strict=True))
    value = ast.literal_eval(node)  # 只接受字符串、数字、布尔、None
    if not isinstance(value, (str, int, float, bool, type(None))):
        raise SkipRecord("non_json_literal")
    return value


def _split_top_level(text: str, separator: str = ",") -> list[str]:
    """按顶层逗号切分，跳过引号和括号里面的逗号。"""
    parts, start, stack, quote, escaped = [], 0, [], None, False
    for i, ch in enumerate(text):
        if quote:  # inside a string: only an unescaped closing quote matters
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack.pop() != {")": "(", "]": "[", "}": "{"}[ch]:
                raise SkipRecord("unbalanced_brackets")
        elif ch == separator and not stack:
            parts.append(text[start:i])
            start = i + 1
    if quote or stack:
        raise SkipRecord("unclosed_argument")
    parts.append(text[start:])
    return parts


def parse_calls(text: str, names: list[str]) -> list[dict]:
    """解析一个调用块，例如 `[Get Coins' Data(limit=10), /lookup(key="a)b")]`。

    工具名可能含空格、撇号、斜杠，所以不能用正则切名字：拿已知工具名做前缀匹配，
    最长的名字优先；括号匹配时要跳过引号里的括号。
    """
    text = text.strip()
    if not (text.startswith("[") and text.endswith("]")):
        raise SkipRecord("not_a_call_block")
    body, calls = text[1:-1].strip(), []
    while body:
        name = next((n for n in sorted(names, key=len, reverse=True) if body.startswith(n + "(")), None)
        if name is None:
            raise SkipRecord("unknown_tool_or_syntax")
        depth, quote, escaped, end = 0, None, False, None
        for i in range(len(name), len(body)):
            ch = body[i]
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                continue
            if ch in "'\"":
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            raise SkipRecord("unclosed_call")
        arguments = {}
        inner = body[len(name) + 1:end]
        for part in _split_top_level(inner) if inner.strip() else []:
            key, sep, value = part.strip().partition("=")
            key = key.strip()
            # 参数名可能含 - 或 $，不一定是合法的 Python 标识符
            if not sep or not re.fullmatch(r"[\w$][\w$.-]*", key) or key in arguments:
                raise SkipRecord("invalid_argument")
            arguments[key] = _literal(ast.parse(value.strip(), mode="eval").body)
        calls.append({"name": name, "arguments": arguments})
        body = body[end + 1:].strip()
        if body:
            if not body.startswith(",") or not body[1:].strip():
                raise SkipRecord("bad_call_separator")
            body = body[1:].strip()
    if not calls:
        raise SkipRecord("empty_call_block")
    return calls


def convert(record: dict) -> dict:
    tools = parse_catalog(record.get("system", ""))
    if any(t["name"] == "think" for t in tools):
        raise SkipRecord("virtual_think_tool")  # “思考”不是真实可执行的工具
    names = [t["name"] for t in tools]
    events = []
    for message in record.get("conversations") or []:
        role = ROLES.get(message.get("from"))
        if role is None or not isinstance(message.get("value"), str):
            raise SkipRecord("unsupported_message")
        event = {"role": role, "content": message["value"]}
        if role == "assistant" and message["value"].strip().startswith("["):
            try:
                event.update(role="tool_call", calls=parse_calls(message["value"], names))
            except (SyntaxError, ValueError) as exc:
                raise SkipRecord("call_block_unparsed") from exc
        events.append(event)
    if not any(e["role"] == "user" for e in events):
        raise SkipRecord("no_user_turn")
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return {"id": hashlib.sha256(raw.encode()).hexdigest()[:16], "tools": tools, "events": events}


def run(raw: Path, out: Path) -> dict:
    episodes, skipped = [], Counter()
    for record in json.loads(Path(raw).read_text()):
        try:
            episodes.append(convert(record))
        except SkipRecord as exc:
            skipped[str(exc).split(":")[0]] += 1
        except (json.JSONDecodeError, SyntaxError, ValueError, TypeError, KeyError):
            skipped["malformed"] += 1
    write_jsonl(out, episodes)
    stats = {"episodes": len(episodes), "skipped": dict(skipped)}
    print(json.dumps(stats))
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.raw, args.out)
