package com.vanilo.psych.agent.service;

import java.util.List;
import java.util.regex.Pattern;
import org.springframework.stereotype.Service;

/** Local quote attribution and action-bound negation, mirrored by Python fixtures. */
@Service
public class RiskDetectionService {
    private static final List<String> TERMS = List.of("想死", "不想活", "自杀", "轻生", "结束自己", "结束生命", "活着没意义", "伤害自己", "割腕", "跳楼", "服药自杀", "准备去死", "马上去死", "kill myself", "hurt myself", "end my life");
    private static final String PAST_OTHER = "(?:朋友|同事|室友)[^，。！？,.!?；;\\n]{0,30}(?:说过|提过|曾说|曾提)";
    private static final Pattern ATTRIBUTION = Pattern.compile("小说|虚构|剧情|影视|电影|角色|主角|新闻|报道|翻译|(?:引用|转述|复述).{0,8}(?:朋友|同事|他人)|(?:朋友|同事|他|她)(?:说|的原话)|" + PAST_OTHER);
    private static final Pattern NONSELF = Pattern.compile("^(?:看到(?:影视|电影)?|新闻|报道|小说人物|小说主角|小说角色|影视人物|电影人物|影视角色|电影角色|角色|主角|文章|朋友说|同事说|他说|她说|我在(?:写小说|翻译)|" + PAST_OTHER + ")");
    private static final Pattern QUOTED = Pattern.compile("“[^”]*”|「[^」]*」|‘[^’]*’|\"[^\"\\n]*\"");
    private static final Pattern OWN_ACTION = Pattern.compile("我(?:也|自己|现在|今晚|此刻|马上|已经|刚刚|刚|真的|同样)*(?:想|要|准备|打算|决定|正在)?(?:马上|现在)?(?:不想活|想死|死|自杀|轻生|伤害自己|结束生命|割腕|跳楼|跳下去|吞|服|吃|喝|写好|准备好)");
    private static final Pattern QUOTE_SOURCE = Pattern.compile("(?:小说|影视|电影|角色|主角|新闻|报道|翻译|朋友|同事|他人|他|她)[^，。！？,.!?；;\\n“”「」‘’\\\"]{0,24}(?:说|写|原话|台词|句子|引述|提到|内容|的话|的经历|翻译(?:剧本)?)[^，。！？,.!?；;\\n“”「」‘’\\\"]{0,8}$|翻译(?:剧本|句子|文本)?[:：]?\\s*$");
    private static final Pattern OWN_REPORT = Pattern.compile("我(?:自己|本人|现在|刚才)?(?:的?(?:想法|情况|经历|原话)|说|告诉你)|这(?:是|就是)我(?:的|现在)|自己.{0,6}原话");
    private static final Pattern DENIAL = Pattern.compile("(?:从来没有|并没有|不再有|没有|不会|并不|不是|不想|从未|没)(?:想要|要|想过|想|任何|有)?(?:想死|死|自杀|轻生|自伤|伤害自己|结束生命|结束自己)的?(?:想法|念头|意图|意思|打算|计划)?|(?:do not|don't|won't|never)\\s+(?:want to\\s+)?(?:kill|hurt) myself");
    private static final Pattern DANGER = Pattern.compile("(?:已经|刚刚|刚才|刚|正在|现在|已)[^，。！？,.!?；;\\n]{0,14}?(?<doseAction>吞|服|吃|喝)[^，。！？,.!?；;\\n]{0,6}?(?:大量|很多|过量|一大把|整瓶)[^，。！？,.!?；;\\n]{0,5}(?:药|药片)|(?:楼顶|天台|屋顶|桥边|桥上|阳台|窗台|高层|高楼)[^。！？.!?；;\\n]{0,30}?(?<heightAction>跳下去|跳下|往下跳|翻过去)|(?:已经|刚|正在)[^，。！？,.!?；;\\n]{0,8}?(?<letterAction>写好|准备好|留好)[^，。！？,.!?；;\\n]{0,3}(?:遗书|告别信)");
    private static final Pattern ACTION_DENIAL_PREFIX = Pattern.compile("(?:没有|没|并未|未曾|从未|不会|不曾|并不|不)(?:真的|实际|已经|正在|刚才|刚刚|刚|打算|准备|要|想|去|在|过|曾|再|了|的|\\s)*$");
    private static final Pattern NO_PREPARATION = Pattern.compile("没有(?:任何|具体)?(?:行动准备|实施准备|实施计划|具体计划|计划)");
    private static final Pattern IMPULSE = Pattern.compile("(?:自伤|伤害自己|轻生|自杀)的?(?:冲动|念头|想法)");
    private static final Pattern IMMINENT_ACTION = Pattern.compile("(?:我(?:现在|此刻|马上|今晚|今天)?(?:就|也|要|会|将|准备|打算|决定)*|(?:现在|此刻|马上|今晚|今天)(?:我|就|要|会|将|准备|打算|决定)*)(?:动手|行动|实施)");
    private static final Pattern RECLAIM = Pattern.compile("我也是(?:这个|同样)|这也是我的|我的真实|不是(?:虚构|剧情|小说)");
    private static final Pattern SENTENCE = Pattern.compile("[。！？.!?；;\\n]");
    private static final Pattern CLAUSE = Pattern.compile("[，。！？,.!?；;\\n]");
    private static final Pattern CLEAN_ACTION = Pattern.compile("[\\s\\\"\\'“”‘’「」]+");
    private static final Pattern CLEAN_RISK = Pattern.compile("[\\s，。！？,.!?、;；:：\\\"\\'“”‘’]+");

    static String scopedText(String text) {
        if (RECLAIM.matcher(text).find()) return text;
        StringBuilder scoped = new StringBuilder();
        var quotes = QUOTED.matcher(text);
        int previous = 0;
        while (quotes.find()) {
            String prefix = text.substring(previous, quotes.start());
            String[] sentences = SENTENCE.split(prefix, -1);
            String introduction = sentences[sentences.length - 1];
            var source = QUOTE_SOURCE.matcher(introduction);
            var own = OWN_REPORT.matcher(introduction);
            int lastOwn = -1;
            while (own.find()) lastOwn = own.start();
            boolean thirdParty = source.find() && lastOwn <= source.start();
            scoped.append(prefix);
            if (!thirdParty) scoped.append(quotes.group());
            previous = quotes.end();
        }
        scoped.append(text.substring(previous));
        StringBuilder owned = new StringBuilder();
        for (String clause : CLAUSE.split(scoped)) {
            if (!(NONSELF.matcher(clause.strip()).find()
                    && !OWN_ACTION.matcher(clause).find() && !OWN_REPORT.matcher(clause).find())) {
                owned.append(clause).append('，');
            }
        }
        return owned.toString();
    }

    static boolean hasConcreteAction(String text) {
        for (String sentence : SENTENCE.split(text)) {
            String clause = CLEAN_ACTION.matcher(sentence).replaceAll("");
            var event = DANGER.matcher(clause);
            while (event.find()) {
                int verb = -1;
                for (String name : List.of("doseAction", "heightAction", "letterAction")) {
                    if (event.group(name) != null) { verb = event.start(name); break; }
                }
                if (verb >= 0 && !ACTION_DENIAL_PREFIX.matcher(clause.substring(0, verb)).find()) return true;
            }
        }
        return false;
    }

    public boolean isHighRisk(String message) {
        if (message == null || message.isBlank()) return false;
        String scoped = scopedText(message.toLowerCase(java.util.Locale.ROOT));
        String unnegated = DENIAL.matcher(scoped).replaceAll("");
        String impulseEvidence = IMPULSE.matcher(unnegated).replaceAll("");
        boolean impulseOnly = NO_PREPARATION.matcher(scoped).find()
                && IMPULSE.matcher(unnegated).find()
                && !OWN_ACTION.matcher(impulseEvidence).find()
                && !IMMINENT_ACTION.matcher(impulseEvidence).find();
        String riskEvidence = impulseOnly ? impulseEvidence : unnegated;
        String normalized = CLEAN_RISK.matcher(riskEvidence).replaceAll("");
        return TERMS.stream().anyMatch(t -> normalized.contains(t) || riskEvidence.contains(t))
                || hasConcreteAction(scoped);
    }
}
