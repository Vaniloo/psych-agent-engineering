package com.vanilo.psych.agent.config;

import com.vanilo.psych.agent.dto.KnowledgeAddRequest;
import com.vanilo.psych.agent.service.KnowledgeService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

@Component
public class KnowledgeSeedRunner implements ApplicationRunner {
    private static final String SOURCE = "seed:psych-faq:v1";
    private static final Logger log = LoggerFactory.getLogger(KnowledgeSeedRunner.class);

    private final KnowledgeService knowledgeService;
    private final boolean enabled;

    public KnowledgeSeedRunner(KnowledgeService knowledgeService,
                               @Value("${psych.seed-knowledge.enabled:true}") boolean enabled) {
        this.knowledgeService = knowledgeService;
        this.enabled = enabled;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (!enabled) {
            return;
        }
        List<KnowledgeAddRequest> seeds = seedItems();
        try {
            Set<String> existingContents = knowledgeService.listAllDocuments().stream()
                    .filter(document -> SOURCE.equals(document.getSource()))
                    .map(document -> document.getContent().strip())
                    .collect(Collectors.toSet());
            if (existingContents.size() >= seeds.size()) {
                return;
            }
            knowledgeService.reindexBySource(SOURCE);
            seeds.stream()
                    .filter(seed -> !existingContents.contains(seed.getContent()))
                    .forEach(knowledgeService::addKnowledge);
        } catch (RuntimeException exception) {
            log.warn("Psychology knowledge seed is temporarily unavailable; startup will continue: {}",
                    exception.getMessage());
        }
    }

    private List<KnowledgeAddRequest> seedItems() {
        return List.of(
                faq("anxiety", """
                        问：突然焦虑、心慌的时候可以怎么做？
                        答：先把注意力放回当下，尝试 4-6 呼吸：吸气 4 秒，呼气 6 秒，重复 3 到 5 分钟。然后说出你看到的 5 样东西、摸到的 4 样东西、听到的 3 种声音，帮助身体从警觉状态回到现实环境。如果胸痛、晕厥或症状持续加重，需要及时就医。
                        """),
                faq("anxiety", """
                        问：总是担心还没发生的事，怎么缓解？
                        答：可以把担心写成两列：我能控制的、我暂时不能控制的。对能控制的部分写下一个最小行动，例如发一封邮件、整理材料、休息十分钟；对不能控制的部分，提醒自己“我现在先不解决它”。这不是逃避，而是把大脑从反复预演中拉回可执行步骤。
                        """),
                faq("stress", """
                        问：学习或工作压力很大，感觉快撑不住了怎么办？
                        答：先做压力分层：必须今天完成、可以延后、可以求助。只保留 1 到 3 个今天真正要做的任务，并把每个任务拆成 25 分钟内可完成的小块。压力很高时，不要用“彻底改变生活”作为目标，先恢复睡眠、饮食和基本节奏。
                        """),
                faq("sleep", """
                        问：失眠时越想睡越睡不着怎么办？
                        答：不要在床上和睡眠较劲。如果躺了二十分钟仍很清醒，可以离开床，到光线较暗的地方做单调、安静的事，困意回来再上床。睡前减少刷手机和激烈讨论，固定起床时间通常比强迫自己早睡更有效。
                        """),
                faq("mood", """
                        问：最近情绪低落，对什么都提不起兴趣怎么办？
                        答：低落时先不要要求自己立刻变积极。可以从低成本行为激活开始：洗澡、出门走十分钟、吃一顿简单的饭、联系一个安全的人。若低落持续两周以上，明显影响学习工作，或伴随自伤念头，建议尽快寻求专业心理咨询或精神科评估。
                        """),
                faq("relationship", """
                        问：和家人或朋友沟通总是吵起来怎么办？
                        答：可以使用“我感受-我需要-我请求”的表达方式，例如“我听到这句话会很紧张，我需要一点时间整理，我希望我们十分钟后再聊”。尽量少用“你总是”“你从来不”，这类句式容易让对方进入防御状态。
                        """),
                faq("self_esteem", """
                        问：总觉得自己不够好怎么办？
                        答：把“我不够好”改写成更具体的问题：我在哪件事上遇到了困难？我缺少什么资源？我下一步能练什么？自我否定常常很笼统，具体化之后才可能行动。也可以每天记录一个完成的小事，训练大脑看见证据而不是只看缺口。
                        """),
                faq("panic", """
                        问：惊恐发作会不会有危险？
                        答：惊恐发作通常会让人感觉心跳很快、喘不过气、快要失控，但它本身多半会在一段时间后下降。可以提醒自己“这是身体警报，不等于真实危险”，同时做缓慢呼气和地面化练习。如果是第一次出现、伴随强烈胸痛或身体疾病风险，应优先做医学检查。
                        """),
                faq("crisis", """
                        问：如果我出现自伤或轻生念头，该怎么办？
                        答：请立刻把自己从危险物品或危险地点旁边移开，联系一个可信任的人陪你，不要独自硬扛。如果有马上行动的冲动，请立即拨打当地紧急电话或前往急诊。你不需要先证明自己“足够严重”才可以求助。
                        """),
                faq("help_seeking", """
                        问：什么时候需要找专业帮助？
                        答：当情绪困扰持续两周以上、睡眠食欲明显变化、学习工作受影响、人际关系严重受损，或出现自伤自杀想法时，建议寻求专业心理咨询、学校心理中心或精神科帮助。寻求帮助不是失败，而是为自己增加支持资源。
                        """),
                faq("grounding", """
                        问：什么是 5-4-3-2-1 地面化练习？
                        答：它是一种把注意力带回当下的方法：说出 5 个看到的东西、4 个摸到的东西、3 个听到的声音、2 个闻到的气味、1 个尝到的味道。适合焦虑、解离感、情绪过载时使用。
                        """),
                faq("boundary", """
                        问：如何建立边界又不伤害关系？
                        答：边界不是惩罚别人，而是说明自己能承受什么。可以用清晰温和的句子：“我愿意听你说，但我现在不能继续被大声指责；如果声音变大，我会先离开十分钟。”边界要具体、可执行，并且前后一致。
                        """),
                faq("exam_anxiety", """
                        问：考试前特别紧张，大脑一片空白怎么办？
                        答：先把复习目标从“全部掌握”改成“完成一个可检查的小单元”，用 25 分钟复习加 5 分钟休息。临考前做几轮呼气略长于吸气的呼吸，并把注意力放在读题、圈关键词、先做会做的题。适度紧张是身体在调动资源，不等于一定会发挥失常。
                        """),
                faq("procrastination", """
                        问：明明知道要做事却一直拖延，怎么开始？
                        答：拖延常和任务模糊、担心失败或即时回报不足有关。任务一直拖着不开始时，先把第一步变小：把任务改写成五分钟内能启动的动作，例如“打开文档写三个标题”，并允许第一版很粗糙。开始后再决定是否继续，比等待动力出现更可靠。
                        """),
                faq("burnout", """
                        问：长期工作很疲惫，对事情越来越麻木，是职业倦怠吗？
                        答：持续耗竭、疏离感和效能感下降可能是倦怠信号，但不能仅凭这些表现自行诊断。可以先记录两周的工作负荷、睡眠和恢复时间，识别哪些要求可以协商、委派或减少。若已经明显影响生活和身体状态，建议与主管、职业健康服务或专业人员讨论支持方案。
                        """),
                faq("social_anxiety", """
                        问：在人多的场合很怕被评价，怎样缓解社交焦虑？
                        答：可以建立从容易到困难的暴露阶梯，例如先向店员问一个问题，再参加小范围交流。每次练习时把目标设为“留在情境中并完成一个动作”，而不是“完全不紧张”。事后记录实际发生的事，检查最担心的结果是否真的出现。
                        """),
                faq("perfectionism", """
                        问：总想做到完美，结果迟迟不敢交付怎么办？
                        答：先为任务定义“足够好”的验收标准和截止时间，把可逆的修改留到反馈之后。可以刻意提交一个 80 分版本，观察真实后果。完美主义常把学习阶段误当成最终审判，而迭代通常比一次做对更有效。
                        """),
                faq("grief", """
                        问：失去重要的人后反复难过，多久才能恢复？
                        答：哀伤没有统一时间表，情绪可能以波浪形式反复出现。先维持吃饭、睡眠、基本联系和纪念仪式，允许自己在安全范围内表达想念。如果长期无法维持基本生活、持续强烈自责，或出现自伤念头，应尽快寻求专业支持。
                        """),
                faq("loneliness", """
                        问：很孤独但又不知道怎么和别人建立联系？
                        答：把“交到亲密朋友”拆成低压力、可重复的接触，例如固定参加一个兴趣活动、每周主动问候一个熟人。关系通常来自多次可预测的小互动。也可以先选择有明确任务的志愿活动或学习小组，减少纯社交带来的压力。
                        """),
                faq("breakup", """
                        问：分手后总想联系对方，情绪反复怎么办？
                        答：先减少会不断触发情绪的即时线索，例如暂时取消特别关注，把想发的信息写在备忘录并延迟二十四小时再决定。安排规律的吃饭、睡眠和与朋友见面，给生活重新建立结构。反复想起并不代表你做错了决定，也不要求你马上放下。
                        """),
                faq("anger", """
                        问：生气时很容易说出伤人的话，怎样停下来？
                        答：注意身体的早期信号，例如心跳加快、声音变大或握拳。一旦出现，可以明确说“我需要暂停十分钟，之后会回来继续谈”，然后离开刺激现场、慢慢呼气、走动或用冷水洗脸。暂停的前提是约定回来沟通，而不是用沉默惩罚对方。
                        """),
                faq("rumination", """
                        问：脑子里一直反复想同一件事，停不下来怎么办？
                        答：可以每天设置十五分钟“担忧时间”，其他时间出现念头时先记下一句话，提醒自己到约定时间再处理。再问自己：现在是在解决问题，还是只是在重复问题？如果有可行动部分，就写下下一步；没有的话，把注意力转向一项具体的感官或身体活动。
                        """),
                faq("nightmare", """
                        问：最近总做噩梦，醒来后很害怕怎么办？
                        答：醒来后先确认当前时间和地点，触摸床单或地面，缓慢说出“梦已经结束，我现在在这里”。白天可以把反复出现的梦写下来，改写成较安全的结局并短暂排练。若噩梦频繁、与创伤经历相关或严重影响睡眠，建议寻求专业评估。
                        """),
                faq("mindfulness", """
                        问：正念练习是不是要让脑子完全没有想法？
                        答：正念不是清空思想，而是注意到念头出现，再把注意力温和地带回呼吸、声音或身体感觉。可以从一分钟开始，每次走神只做一次“发现并返回”。练习目标是改变与念头的关系，不是强迫自己停止思考。
                        """),
                faq("exercise", """
                        问：运动能帮助情绪吗，应该从多少开始？
                        答：规律活动可能帮助睡眠、压力和情绪，但不需要一开始就高强度。可以从每天十分钟快走、拉伸或跟随简单视频开始，以能持续为优先。若有身体疾病、胸痛、晕厥或运动禁忌，应先咨询医疗专业人员。
                        """),
                faq("digital_overload", """
                        问：刷手机越刷越焦虑，但又停不下来怎么办？
                        答：先减少触发而不是只靠意志力：关闭非必要通知，把高频应用移出首页，并为睡前一小时设置替代活动。可以规定一天两到三个查看新闻或社交媒体的时间段。若破功，不必全盘放弃，只需回到下一次计划好的停点。
                        """),
                faq("workplace_conflict", """
                        问：和同事发生冲突，之后一直很紧张怎么办？
                        答：先区分事实、自己的解释和希望达成的结果。沟通时描述具体行为及影响，例如“昨天会议中我三次没有说完，我希望下次先让我把观点讲完”，避免推测动机。若存在持续羞辱、威胁或权力滥用，应保留记录并寻求人力或正式支持渠道。
                        """),
                faq("counseling", """
                        问：第一次去心理咨询，不知道该说什么怎么办？
                        答：第一次咨询不需要准备完整故事，可以从最近最困扰的一件事、持续多久、对生活有什么影响开始。也可以提前写下希望获得的帮助、用药和就医经历、以及你不想讨论的边界。咨询关系需要匹配，你可以询问方法、保密范围和后续计划。
                        """),
                faq("self_compassion", """
                        问：犯错后一直责怪自己，怎么对自己宽容一点？
                        答：自我宽容不是推卸责任。可以依次说出事实、影响和修复行动，再用你会对朋友说的话回应自己。例如“我确实遗漏了这件事，我会道歉并补救；一次错误不等于我是个糟糕的人”。把惩罚自己改成承担和学习。
                        """),
                faq("decision_fatigue", """
                        问：事情太多，连小决定都让我很累怎么办？
                        答：决策疲劳时可以减少当天需要临时选择的数量：固定简单早餐、提前列出三项优先任务、把不重要的选择设置默认项。对重要决定使用明确的截止时间和两到三个标准，避免无限收集信息。疲惫时先恢复基本状态，再处理不可逆的大决定。
                        """)
        );
    }

    private KnowledgeAddRequest faq(String category, String content) {
        return new KnowledgeAddRequest(content.strip(), category, SOURCE);
    }
}
