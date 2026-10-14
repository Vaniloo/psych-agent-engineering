package com.vanilo.psych.agent.service;

import java.util.*;
import java.math.BigInteger;
import java.util.regex.MatchResult;
import java.util.regex.Pattern;

/** Conservative literal event bindings; an accepted model choice never authorizes an ambiguous write. */
public final class GoalEvidencePolicy {
    private GoalEvidencePolicy() {}
    private static final Pattern META = Pattern.compile("(?:不要|不用|无需|不必|别|不想|不需要)[^，,。！？\\n]{0,10}(?:同步|记录|更新|修改|改动|保存|写入)|只是(?:回顾|复述|引用)|旧记录|系统.{0,12}记录|这是.{0,8}(?:示例|假设|举例)|举例|引用|复述|假如|如果|只是在读|已读完|不代表做");
    private static final Pattern OTHER = Pattern.compile("(?:^|[，。！？；;])\\s*(?:她|他|(?:我(?:的)?)?(?:朋友|同事|同学|室友|表弟|妹妹|哥哥|姐姐|弟弟|妈妈|爸爸|父亲|母亲|家人|伴侣|丈夫|妻子|导师|老师))|这是(?:她|他|朋友|同事).{0,4}做的");
    private static final Pattern NEGATION = Pattern.compile("没有|没|未|不是|不曾|不代表");
    private static final Pattern CONTRADICTS_DONE = Pattern.compile("一点|部分|还没|尚未|未完成|没完成|没做完|卡住|阻碍|坏了|没法|不能算完成|选择休息|先休息|暂时休息");
    private static final Pattern ACTUAL = Pattern.compile("已经|刚刚|刚才|刚.{0,8}了|今天.*(?:了|卡住|休息)|昨天.*了|完成|做完|做了一点|试了一点|写好|发出|做了|充了|走了|读了|写了|休息一次|(?:打开|拿起|拿出|起草|散步|联系|离开|放|收|标|写|发|做|读|走|充|整理|浇|关).{0,12}(?:了|好|完|出去|结束)");
    private static final Pattern PAST_CREATION = Pattern.compile("(?:昨天|之前|以前|上次|已经).{0,14}(?:创建|建立|添加|设定|设置|制定|保存|新建).{0,10}(?:过|了)|只是回顾|别再创建");
    private static final Pattern QUANTITY = Pattern.compile("([零〇一二两三四五六七八九十百\\d]+)(分钟|小时|秒|页|条|件|个|杯|次|句|段)");
    private static final Pattern VERB = Pattern.compile("离开|充电|散步|整理|练习|关闭|打开|起草|联系|记录|休息|写|发|放|拿|读|标|喝|吃|走|收|浇");
    private static final Pattern RECIPIENT = Pattern.compile("(?:给|向)([^，。！？；;把将的]{1,16}?)(?:发|写|联系|把|将|的)");
    private static final Pattern PLACEMENT = Pattern.compile("(?:把|将)(.{1,16}?)(?:放(?:到|进|入|回|在)?)(.+)");
    private static final Pattern FRONT_PLACEMENT = Pattern.compile("(.{1,16}?)(?:被我|我)(?:刚|现在)*(?:放(?:到|进|入|回|在)?)(.+)");
    private static final Pattern WORKPLACE = Pattern.compile("在([^，。！？；;上里内]{1,16}?)(?:[上里内]|(?:离开|充电|散步|整理|练习|关闭|打开|起草|联系|记录|休息|写|发|放|拿|读|标|喝|吃|走|收|浇))");
    private static final Pattern FUTURE = Pattern.compile("打算|希望|想(?:要|去|做|写|发|放|读|走|练习)|准备|计划|明天|以后");
    private static final Pattern CLAUSES = Pattern.compile("[，。！？；;,.!?\\n]|但是|不过|然而|随后|接着|然后|并且|同时|又|却|但");
    private static final String VERBS = "离开|充电|散步|整理|练习|关闭|打开|起草|联系|记录|休息|写|发|放|拿|读|标|喝|吃|走|收|浇";

    public static boolean pastCreation(String text) { return PAST_CREATION.matcher(text).find(); }

    public static boolean eligible(String text) {
        return text != null && !text.isBlank() && text.length() <= 500 && ACTUAL.matcher(text).find()
                && VERB.matcher(canonical(text)).find()
                && !META.matcher(text).find() && !OTHER.matcher(text).find();
    }

    private static String canonical(String text) {
        for (int i=0; i<10; i++) text = text.replace((char)('０'+i), (char)('0'+i));
        return text.replaceAll("写下|写出|写好|写完", "写")
                .replaceAll("发送|发出", "发").replaceAll("标出|标记", "标")
                .replaceAll("走一走|走了走|走走", "走")
                .replaceAll("拿到|拿去|收进", "放到")
                .replaceAll("充(?:了)?(?=[零〇一二两三四五六七八九十百\\d]+(?:分钟|小时|秒))", "充电")
                .replaceAll("问候(?:消息|短信)|问候文字", "问候")
                .replaceAll("近况(?:消息|短信)", "近况")
                .replaceAll("收([^，。！？；;]{1,16}?)到", "把$1放到")
                .replaceAll("\\s|今天|昨天|已经|刚刚|刚才|午后|起床后|一下|了", "");
    }

    private static List<String> anchors(String title) {
        String content = QUANTITY.matcher(canonical(title)).replaceAll("").replace("的", "");
        var tokens = Pattern.compile(VERBS + "|把|将|给|向|在|到|进|入|回").matcher(content);
        Set<String> prepositions = Set.of("把", "将", "给", "向", "在", "到", "进", "入", "回");
        List<String> result = new ArrayList<>();
        int previous = 0;
        while (tokens.find()) {
            if (tokens.start() > previous) result.add(content.substring(previous, tokens.start()));
            if (!prepositions.contains(tokens.group())) result.add(tokens.group());
            previous = tokens.end();
        }
        if (previous < content.length()) result.add(content.substring(previous));
        return result;
    }

    private static BigInteger number(String value) {
        if (value.matches("[0-9]+")) return new BigInteger(value);
        String digits = "零〇一二两三四五六七八九";
        int[] values = {0,0,1,2,2,3,4,5,6,7,8,9};
        int total = 0, current = 0;
        for (char c : value.toCharArray()) {
            if (c == '十' || c == '百') {
                total += (current == 0 ? 1 : current) * (c == '十' ? 10 : 100);
                current = 0;
            } else {
                int i = digits.indexOf(c);
                current = i >= 0 ? values[i] : 0;
            }
        }
        return BigInteger.valueOf(total + current);
    }

    private static String quantityVerb(String text, MatchResult quantity) {
        var preceding = VERB.matcher(text.substring(0, quantity.start()));
        String verb = null;
        while (preceding.find()) verb = preceding.group();
        var following = VERB.matcher(text.substring(quantity.end()));
        return verb != null ? verb : (following.find() ? following.group() : null);
    }

    private static String destination(String value) {
        value = value.split("这一步|这个行动|这件事|卡住|做完|完成|结束", -1)[0];
        return VERB.split(QUANTITY.matcher(value).replaceAll(""), -1)[0].replaceAll("[上里内]$", "");
    }

    private static MatchResult first(String pattern, String text) {
        var matcher = Pattern.compile(pattern).matcher(text);
        return matcher.find() ? matcher.toMatchResult() : null;
    }

    private static boolean bindingMatches(String title, String clause, String outcome, String context) {
        title = canonical(title);
        clause = canonical(clause);
        if (!VERB.matcher(title).find()) return false;
        List<String> required = new ArrayList<>(anchors(title));
        var recipient = RECIPIENT.matcher(title);
        if (recipient.find()) {
            var observed = RECIPIENT.matcher(clause).results().toList();
            List<String> bound = new ArrayList<>();
            for (var r : observed) bound.add(r.group(1));
            var received = first("^(.{1,16}?)收到我发", clause);
            var greeting = first("问候(.{1,16}?)的?消息", clause);
            var chat = first("(?:打开|点开)(.{1,16}?)的?聊天框", clause);
            if (bound.isEmpty()) {
                for (var r : Arrays.asList(received,greeting,chat)) if (r != null) bound.add(r.group(1));
            }
            if (!bound.equals(List.of(recipient.group(1)))) return false;
            if ("partial".equals(outcome) && (clause.contains("写") || clause.contains("起草"))) {
                String verb = clause.contains("起草") ? "起草" : "写";
                required.replaceAll(a -> a.equals("发") ? verb : a);
            }
            if ("partial".equals(outcome) && chat != null) {
                required.removeAll(List.of(recipient.group(1),"发"));
                for (String c : CLAUSES.split(canonical(context))) {
                    if (!Pattern.compile("还没|尚未").matcher(c).find() || !c.contains("发")) continue;
                    String literal = QUANTITY.matcher(c).replaceAll("").replace("的", "");
                    if (required.stream().allMatch(literal::contains)) return true;
                }
                return false;
            }
        }
        var placement = PLACEMENT.matcher(title);
        if (placement.find()) {
            var observed = PLACEMENT.matcher(clause).results().toList();
            if (observed.isEmpty()) observed = FRONT_PLACEMENT.matcher(clause).results().toList();
            String targetPlace = destination(placement.group(2));
            if (observed.isEmpty() && "partial".equals(outcome)) {
                return first("拿(?:起|出)"+Pattern.quote(placement.group(1)),clause) != null
                        && first("(?:还没|尚未).{0,6}(?:放|收).{0,3}"+Pattern.quote(targetPlace),canonical(context)) != null;
            }
            if (observed.size() != 1 || !observed.get(0).group(1).equals(placement.group(1))) return false;
            if (!destination(observed.get(0).group(2)).equals(targetPlace)) return false;
            if ("partial".equals(outcome)) required = new ArrayList<>(anchors(title.substring(0,placement.start(2))+targetPlace));
        }
        var workplace = WORKPLACE.matcher(title);
        if (workplace.find()) {
            String place = workplace.group(1).replaceAll("[上里内]$", "");
            var observed = WORKPLACE.matcher(clause);
            boolean found = observed.find();
            if (found && !observed.group(1).replaceAll("[上里内]$", "").equals(place)) return false;
            required.replaceAll(a -> a.replaceAll("[上里内]$", "").equals(place) ? place : a);
            if (Set.of("blocked","rest").contains(outcome)
                    || ("partial".equals(outcome) && !found && first(Pattern.quote(place)+".{0,4}(?:这一步|这个行动)",canonical(context)) != null)) {
                required.removeIf(place::equals);
            }
        }
        if (required.isEmpty()) return false;
        String literal = QUANTITY.matcher(clause).replaceAll("").replace("的", "");
        for (String anchor : required) if (!literal.contains(anchor)) return false;
        if ("done".equals(outcome)) {
            var amounts = QUANTITY.matcher(clause).results().toList();
            var quantities = QUANTITY.matcher(title);
            while (quantities.find()) {
                boolean covered = false;
                boolean matching = false;
                for (var q : amounts) {
                    if (q.group(2).equals(quantities.group(2))
                            && Objects.equals(quantityVerb(clause,q), quantityVerb(title,quantities))) {
                        matching = true;
                        if (number(q.group(1)).compareTo(number(quantities.group(1))) >= 0) covered = true;
                    }
                }
                if (!matching && RECIPIENT.matcher(title).find()
                        && "条".equals(quantities.group(2)) && BigInteger.ONE.equals(number(quantities.group(1)))) covered = true;
                if (!covered) return false;
            }
        }
        return true;
    }

    public static boolean supports(String text, String actionTitle, String outcome) {
        if (actionTitle == null || actionTitle.isBlank() || outcome == null || !eligible(text)
                || !Set.of("done", "partial", "blocked", "rest").contains(outcome)) return false;
        if ("done".equals(outcome) && CONTRADICTS_DONE.matcher(text).find()) return false;
        if (!"rest".equals(outcome) && Pattern.compile("(?:选择|先|暂时).{0,3}(?:休息|歇)|暂停这次").matcher(text).find()) return false;
        String evidence = text.replaceAll("“[^”]*”|「[^」]*」|‘[^’]*’|\"[^\"\\n]*\"", "");
        boolean blockedOrRest = Set.of("blocked","rest").contains(outcome)
                && Pattern.compile("卡住|坏了|阻碍|没法|休息").matcher(evidence).find();
        List<String> clauses = Arrays.stream(CLAUSES.split(evidence)).filter(c -> (!FUTURE.matcher(c).find()
                || (blockedOrRest && Pattern.compile("这一步|这个行动|这件事").matcher(c).find()))
                && (ACTUAL.matcher(c).find() || (Pattern.compile("我|本人|自己").matcher(c).find() && VERB.matcher(c).find()))).toList();
        List<String> steps = Arrays.stream(actionTitle.split("[，。；;]")).filter(s -> !s.isBlank()).toList();
        List<Boolean> supported = new ArrayList<>();
        for (String step : steps) {
            boolean found = false;
            for (String clause : clauses) if (bindingMatches(step,clause,outcome,evidence)) found = true;
            supported.add(found);
        }
        if (supported.isEmpty() || ("done".equals(outcome) ? supported.contains(false) : !supported.contains(true))) return false;
        if (Pattern.compile("(?:不知道|不清楚|不确定|没说|没有说).{0,12}(?:给谁|是谁|哪|目标|行动)").matcher(text).find()) return false;
        Set<String> titleAnchors = new HashSet<>();
        for (String step : steps) for (String a : anchors(step)) if (!VERB.matcher(a).matches()) titleAnchors.add(a);
        for (String clause : CLAUSES.split(text)) {
            if (!NEGATION.matcher(clause).find()) continue;
            if ("partial".equals(outcome) && Pattern.compile("还没|尚未|没(?:完全)?(?:完成|做完|写完|读完|发出去)|未完成|十分钟还没到").matcher(clause).find()) continue;
            if (Set.of("blocked","rest").contains(outcome)) continue;
            String normalized = canonical(clause);
            for (String a : titleAnchors) if (normalized.contains(a)) return false;
        }
        return true;
    }
}
