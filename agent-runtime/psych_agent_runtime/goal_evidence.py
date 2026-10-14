"""Application-owned vetoes on automatic goal writes; never create IDs or outcomes.

A model may select a candidate, but a third-party report, replay, explicit denial
of that action or an incompatible outcome cannot authorize a write. Uncertain
expressions remain available for manual confirmation in the goal UI.
"""

import re

META = re.compile(
    r"(?:不要|不用|无需|不必|别|不想|不需要)[^，,。！？\n]{0,10}(?:同步|记录|更新|修改|改动|保存|写入)|只是(?:回顾|复述|引用)|旧记录|系统.{0,12}记录|这是.{0,8}(?:示例|假设|举例)|举例|引用|复述|假如|如果|只是在读|已读完|不代表做"
)
OTHER = re.compile(
    r"(?:^|[，。！？；;])\s*(?:她|他|(?:我(?:的)?)?(?:朋友|同事|同学|室友|表弟|妹妹|哥哥|姐姐|弟弟|妈妈|爸爸|父亲|母亲|家人|伴侣|丈夫|妻子|导师|老师))|这是(?:她|他|朋友|同事).{0,4}做的"
)
NEGATION = re.compile(r"没有|没|未|不是|不曾|不代表")
CONTRADICTS_DONE = re.compile(
    r"一点|部分|还没|尚未|未完成|没完成|没做完|卡住|阻碍|坏了|没法|不能算完成|选择休息|先休息|暂时休息"
)
ACTUAL = re.compile(
    r"已经|刚刚|刚才|刚.{0,8}了|今天.*(?:了|卡住|休息)|昨天.*了|完成|做完|做了一点|试了一点|写好|发出|做了|充了|走了|读了|写了|休息一次|(?:打开|拿起|拿出|起草|散步|联系|离开|放|收|标|写|发|做|读|走|充|整理|浇|关).{0,12}(?:了|好|完|出去|结束)"
)
PAST_CREATION = re.compile(
    r"(?:昨天|之前|以前|上次|已经).{0,14}(?:创建|建立|添加|设定|设置|制定|保存|新建).{0,10}(?:过|了)|只是回顾|别再创建"
)


def eligible_expression(text):
    return (
        0 < len(text.strip()) <= 500
        and bool(ACTUAL.search(text))
        and bool(VERB.search(_canonical(text)))
        and not (META.search(text) or OTHER.search(text))
    )


QUANTITY = re.compile(r"([零〇一二两三四五六七八九十百\d]+)(分钟|小时|秒|页|条|件|个|杯|次|句|段)")
VERBS = r"离开|充电|散步|整理|练习|关闭|打开|起草|联系|记录|休息|写|发|放|拿|读|标|喝|吃|走|收|浇"
VERB = re.compile(VERBS)
RECIPIENT = re.compile(r"(?:给|向)([^，。！？；;把将的]{1,16}?)(?:发|写|联系|把|将|的)")
PLACEMENT = re.compile(r"(?:把|将)(.{1,16}?)(?:放(?:到|进|入|回|在)?)(.+)")
FRONT_PLACEMENT = re.compile(r"(.{1,16}?)(?:被我|我)(?:刚|现在)*(?:放(?:到|进|入|回|在)?)(.+)")
WORKPLACE = re.compile(r"在([^，。！？；;上里内]{1,16}?)(?:[上里内]|(?:" + VERBS + r"))")
FUTURE = re.compile(r"打算|希望|想(?:要|去|做|写|发|放|读|走|练习)|准备|计划|明天|以后")
CLAUSES = re.compile(r"[，。！？；;,.!?\n]|但是|不过|然而|随后|接着|然后|并且|同时|又|却|但")


def _number(value):
    if value.isascii() and value.isdigit():
        return int(value)
    digits = dict(
        zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9), strict=True)
    )
    total, current = 0, 0
    for char in value:
        if char in {"十", "百"}:
            total += (current or 1) * (10 if char == "十" else 100)
            current = 0
        else:
            current = digits.get(char, 0)
    return total + current


def _canonical(text):
    text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    for pattern, replacement in [
        (r"写下|写出|写好|写完", "写"),
        (r"发送|发出", "发"),
        (r"标出|标记", "标"),
        (r"走一走|走了走|走走", "走"),
        (r"拿到|拿去|收进", "放到"),
        (r"充(?:了)?(?=[零〇一二两三四五六七八九十百\d]+(?:分钟|小时|秒))", "充电"),
        (r"问候(?:消息|短信)", "问候"),
        (r"问候文字", "问候"),
        (r"近况(?:消息|短信)", "近况"),
        (r"收([^，。！？；;]{1,16}?)到", r"把\1放到"),
    ]:
        text = re.sub(pattern, replacement, text)
    return re.sub(r"\s|今天|昨天|已经|刚刚|刚才|午后|起床后|一下|了", "", text)


def _anchors(title):
    # All literal content anchors are required, not one shared character pair.
    # Unknown paraphrases deliberately need manual confirmation. Quantities are
    # checked separately so a true partial step can report a smaller amount.
    content = QUANTITY.sub("", _canonical(title)).replace("的", "")
    parts = re.split(r"(" + VERBS + r"|把|将|给|向|在|到|进|入|回)", content)
    return [
        p for p in parts if p and p not in {"把", "将", "给", "向", "在", "到", "进", "入", "回"}
    ]


def _binding_matches(title, clause, outcome, context=""):
    title, clause = _canonical(title), _canonical(clause)
    if not VERB.search(title):
        return False
    required = _anchors(title)
    recipient = RECIPIENT.search(title)
    if recipient:
        observed = list(RECIPIENT.finditer(clause))
        # Alternate grammar must still explicitly bind the addressee to the
        # user's sending, never just mention their name elsewhere.
        received = re.search(r"^(.{1,16}?)收到我发", clause)
        greeting = re.search(r"问候(.{1,16}?)的?消息", clause)
        chat = re.search(r"(?:打开|点开)(.{1,16}?)的?聊天框", clause)
        bound = [r.group(1) for r in observed]
        if not bound:
            bound = [r.group(1) for r in (received, greeting, chat) if r]
        if bound != [recipient.group(1)]:
            return False
        if outcome == "partial" and ("写" in clause or "起草" in clause):
            required = [
                ("起草" if "起草" in clause else "写") if a == "发" else a for a in required
            ]
        if outcome == "partial" and chat:
            # Opening the named recipient's chat is preparation only when the
            # same report explicitly names the unfinished message content.
            themes = [a for a in required if a not in {recipient.group(1), "发"}]
            unfinished = [
                c
                for c in CLAUSES.split(_canonical(context))
                if re.search(r"还没|尚未", c) and "发" in c
            ]
            return any(
                all(a in QUANTITY.sub("", c).replace("的", "") for a in themes) for c in unfinished
            )
    placement = PLACEMENT.search(title)
    if placement:
        observed = list(PLACEMENT.finditer(clause))
        if not observed:
            observed = list(FRONT_PLACEMENT.finditer(clause))

        def destination(value):
            value = re.split(r"这一步|这个行动|这件事|卡住|做完|完成|结束", value)[0]
            return re.sub(r"[上里内]$", "", VERB.split(QUANTITY.sub("", value))[0])

        target_place = destination(placement.group(2))
        # Starting a placement step can mean picking up that exact object, but
        # requires an explicit unfinished reference to the intended destination.
        if not observed and outcome == "partial":
            picked_up = re.search(r"拿(?:起|出)" + re.escape(placement.group(1)), clause)
            unfinished = re.search(
                r"(?:还没|尚未).{0,6}(?:放|收).{0,3}" + re.escape(target_place), _canonical(context)
            )
            return bool(picked_up and unfinished)
        if len(observed) != 1 or observed[0].group(1) != placement.group(1):
            return False
        # Stop the destination at a subsequent action, e.g. 客厅 + 充电.
        if destination(observed[0].group(2)) != target_place:
            return False
        if outcome == "partial":
            required = _anchors(title[: placement.start(2)] + target_place)
    workplace = WORKPLACE.search(title)
    if workplace:
        place = re.sub(r"[上里内]$", "", workplace.group(1))
        observed = WORKPLACE.search(clause)
        if observed and re.sub(r"[上里内]$", "", observed.group(1)) != place:
            return False
        # An explicit blocked/rest report can omit the workspace, but cannot
        # name a different one. Actual done/partial evidence must include it.
        required = [place if re.sub(r"[上里内]$", "", a) == place else a for a in required]
        if outcome in {"blocked", "rest"} or (
            outcome == "partial"
            and not observed
            and re.search(re.escape(place) + r".{0,4}(?:这一步|这个行动)", _canonical(context))
        ):
            required = [a for a in required if a != place]
    literal = QUANTITY.sub("", clause).replace("的", "")
    if not required or not all(anchor in literal for anchor in required):
        return False
    if outcome == "done":

        def quantity_verb(text, quantity):
            preceding = list(VERB.finditer(text[: quantity.start()]))
            following = VERB.search(text[quantity.end() :])
            return (
                preceding[-1].group() if preceding else (following.group() if following else None)
            )

        amounts = list(QUANTITY.finditer(clause))
        for quantity in QUANTITY.finditer(title):
            matching = [
                q
                for q in amounts
                if q.group(2) == quantity.group(2)
                and quantity_verb(clause, q) == quantity_verb(title, quantity)
            ]
            # A completed, explicitly addressed message entails at least one
            # message; no implicit duration, volume or larger count is assumed.
            if (
                not matching
                and recipient
                and quantity.group(2) == "条"
                and _number(quantity.group(1)) == 1
            ):
                continue
            if not matching or not any(
                _number(q.group(1)) >= _number(quantity.group(1)) for q in matching
            ):
                return False
    return True


def supports_progress(text, action_title, outcome):
    if not isinstance(action_title, str) or not action_title.strip():
        return False
    if not eligible_expression(text) or outcome not in {"done", "partial", "blocked", "rest"}:
        return False
    if outcome == "done" and CONTRADICTS_DONE.search(text):
        return False
    if outcome != "rest" and re.search(r"(?:选择|先|暂时).{0,3}(?:休息|歇)|暂停这次", text):
        return False
    # A quoted step title or a future intention is context, not an action event.
    evidence = re.sub(r'“[^”]*”|「[^」]*」|‘[^’]*’|"[^"\n]*"', "", text)
    clauses = [
        c
        for c in CLAUSES.split(evidence)
        if (
            not FUTURE.search(c)
            or (
                outcome in {"blocked", "rest"}
                and re.search(r"这一步|这个行动|这件事", c)
                and re.search(r"卡住|坏了|阻碍|没法|休息", evidence)
            )
        )
        and (ACTUAL.search(c) or (re.search(r"我|本人|自己", c) and VERB.search(c)))
    ]
    steps = [s for s in re.split(r"[，。；;]", action_title) if s.strip()]
    supported = [any(_binding_matches(s, c, outcome, evidence) for c in clauses) for s in steps]
    if not supported or not (all(supported) if outcome == "done" else any(supported)):
        return False
    if re.search(r"(?:不知道|不清楚|不确定|没说|没有说).{0,12}(?:给谁|是谁|哪|目标|行动)", text):
        return False
    title_anchors = {a for s in steps for a in _anchors(s) if not VERB.fullmatch(a)}
    for clause in CLAUSES.split(text):
        if not NEGATION.search(clause):
            continue
        # Partial completion can explicitly name an unfinished remainder. Plain
        # denials of the action/object still veto, including '没写烦心事'.
        if outcome == "partial" and re.search(
            r"还没|尚未|没(?:完全)?(?:完成|做完|写完|读完|发出去)|未完成|十分钟还没到", clause
        ):
            continue
        if outcome in {"blocked", "rest"}:
            continue
        if any(anchor in _canonical(clause) for anchor in title_anchors):
            return False
    return True
