package com.vanilo.psych.agent.service;

import java.util.regex.Pattern;
import java.util.List;

/** The adjacent saved exchange can bind a reference, but cannot assert completion. */
public final class GoalReferencePolicy {
    private GoalReferencePolicy() {}
    private static final Pattern STATUS = Pattern.compile(
            "(?:ok|好的|好|嗯)?(?:那|这一步|这个步骤)?(?:我)?"
            + "(?:今天|刚刚|刚才|已经)?(?:这一步|这个步骤)?(?:已经)?"
            + "(?<status>完成了|做完了|搞定了|做了一点|做了一部分)(?:啦|呢|哦)?",
            Pattern.CASE_INSENSITIVE);
    private static final Pattern STEP_QUERY = Pattern.compile(
            "(?:那|请|你说说)?(?:第(?<ordinal>[一二三四五六七八九十1-9])(?:个)?|当前|现在的|这)?"
            + "(?:小)?(?:步骤|行动)(?:是什么|是啥|怎么做|要做什么|呢)[？?。！!\\s]*");
    private static final Pattern NUMBER_TAG = Pattern.compile(
            "(?:第(?<before>[一二三四五六七八九十\\d]+)(?:个)?(?:小)?(?:步骤|步|行动)|"
            + "(?:小)?(?:步骤|行动)(?<after>[一二三四五六七八九十\\d]+))");
    private record Report(String outcome,Integer position) {}

    public static String outcome(String message) {
        var report=report(message);return report==null?null:report.outcome();
    }

    public static Integer reportedPosition(String message) {
        var report=report(message);return report==null?null:report.position();
    }

    private static Report report(String message) {
        if (message == null || message.length()>500 || message.contains("?") || message.contains("？")) return null;
        for(int i=0;i<10;i++)message=message.replace((char)('０'+i),(char)('0'+i));
        message=message.replaceAll("\\s", "");
        var tags=NUMBER_TAG.matcher(message);Integer position=null;
        if(tags.find()) {
            int start=tags.start(),end=tags.end();String value=tags.group("before")!=null?tags.group("before"):tags.group("after");
            if(tags.find())return null;
            int number;
            if(value.matches("[0-9]+")) {
                try {number=Integer.parseInt(value);} catch(NumberFormatException e){return null;}
            } else number=List.of("一","二","三","四","五","六","七","八","九","十","十一","十二").indexOf(value)+1;
            if(number<1||number>12)return null;
            position=number-1;message=message.substring(0,start)+message.substring(end);
        }
        var match = STATUS.matcher(message.replaceAll("[\\s，,。.!！]", ""));
        if (!match.matches()) return null;
        return new Report(match.group("status").equals("做了一点") || match.group("status").equals("做了一部分")
                ? "partial" : "done",position);
    }

    public static boolean stepQuery(String message) {
        return message != null && STEP_QUERY.matcher(message.strip()).matches();
    }

    public static int position(String message) {
        var match=STEP_QUERY.matcher(message.strip());
        if (!match.matches() || match.group("ordinal") == null) return -1;
        String ordinal=match.group("ordinal");
        return Character.isDigit(ordinal.charAt(0)) ? Integer.parseInt(ordinal)-1
                : "一二三四五六七八九十".indexOf(ordinal);
    }

    public static boolean mentions(String reply, String title) {
        return reply != null && title != null && !title.isBlank()
                && normalize(reply).contains(normalize(title));
    }

    private static String normalize(String text) {
        return text.replaceAll("[\\s，,。.!！?？：:；;、]", "").replace("或者", "或");
    }
}
