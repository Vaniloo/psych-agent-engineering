"""Carry an explicitly named owned goal into short conversational follow-ups."""

import re

TITLE = re.compile(
    r"目标\s*(?:「([^」\n]{1,200})」|“([^”\n]{1,200})”|‘([^’\n]{1,200})’|\"([^\"\n]{1,200})\")"
)
READ = re.compile(
    r"聊|总结|回顾|进展|进度|状态|完成|结束|步骤|下一步|查|看|设置|设成|设为|创建|新建|调用工具|用工具"
)
EXCLUDED = re.compile(
    r"他说|她说|例如|举例|假如|引用|(?:不要|不用|无需|别)[^。！？\n]{0,12}(?:查|看|回顾).*目标|重新开始|换个话题"
)


def named_goal(message):
    if EXCLUDED.search(message):
        return None
    match = TITLE.search(message)
    return next((s.strip() for s in match.groups() if s), None) if match else None


def goal_focus(message, context=None):
    if EXCLUDED.search(message) or not READ.search(message):
        return None
    current = named_goal(message)
    if not current and goal_list_request(message):
        return None
    if current:
        prefix = message[: TITLE.search(message).start()]
        if re.search(r"创建|新建|设置|建立", prefix) and not re.search(
            r"基于|根据|参考|延续", prefix
        ):
            return None
        return current
    if re.search(r"创建|新建|设置|建立", message) and not re.search(
        r"下一步|后续|接下来|下一个", message
    ):
        return None
    if context:
        for item in reversed(context.messages[-40:]):
            if item.role != "user":
                continue
            if re.search(r"重新开始|换个话题", item.content):
                break
            if title := named_goal(item.content):
                return title
    return None


def goal_list_request(message):
    """An owned list query is independent of the last conversation's named goal."""
    return not EXCLUDED.search(message) and any(_goal_list_clause(clause) for clause in re.split(r"[；;。！？\n]", message))


def _goal_list_clause(message):
    return (
        not EXCLUDED.search(message)
        and not re.search(r"(?:不要|不用|无需|不必|别|不想).{0,16}(?:查|看|列|回顾)", message)
        and bool(re.search(r"目标", message))
        and bool(re.search(r"查|看|列|回顾|盘点|总结|哪些|什么|几个|多少|有哪些", message))
        and not re.search(r"这个|那个|该目标|这一|下一步|后续|下一个", message)
        and not re.search(r"什么是|怎么(?:创建|设置)|如何|他说|她说|别人的|朋友的", message)
    )
