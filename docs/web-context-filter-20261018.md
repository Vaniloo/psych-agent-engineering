# 两级网页上下文筛选（本地实验）

更新：角色语气搜索按用户要求暂停，默认`WEB_ROLE_VOICE_ENABLED=false`，有卡片也不自动联网查语气；相关代码冻结保留，暂不继续优化。当前只对白名单开发通用搜索和主动搜索。`WEB_SEARCH_PROACTIVE_ENABLED=true`时由生成模型判断实时/外部来源需要，不要求用户明确说“搜索”；稳定常识和内部工具任务不主动外查。固定工具名单与后训练建议见[注册表审查](tool-registry-review-20261018.md)。

本地真实SSE验证：未显式说“搜索”的文档链接请求触发1次search_web，7候选/读3页/预选4块，搜索工具约4.39秒、整轮约6.99秒；明确“不用联网”的概念问题不外查；两轮均没有角色语气搜索且保存成功。这是功能样例，不是全量工具质量测试。远程Router/Executor模型当前无法连接，第二轮内部降级路径仍出现无关心理知识/资源读取，该限制已作为后续工具选择负例，不能把这次验证说成完整远程模型链通过。

目标是把外部搜索变成有来源、可预算、可观察的读取链路。SearXNG 官方镜像负责汇总搜索源，Trafilatura 提取正文，本地 Laya 分别判断标题摘要和正文 chunk 的用途。未复制或依赖 GPT Researcher 整个应用，借鉴了其 context filter 的分阶段检索思想；不使用官方 Jev/TypeSafe API。

## 执行链路

`Agent loop → search_web / search_role_voice → SearXNG → 标题摘要评分 → 至多3页正文 → chunk评分 → 预算内完整片段 → 原Agent loop观察结果 → 回复`

- 两个工具共用同一实现和模型 worker；角色检索只对外发送角色名和表达主题，不发送聊天、账号、记忆。
- 标题层最多10条，仅用于读取优先级，不按未校准分数硬删候选。保留一个词法候选。
- 每页读取上限1MB，提取正文上限24,000字符。700字符窗口、100字符重叠，兼顾词法相关和分散位置，每页最多8块进入模型，共最多24块。未覆盖所有正文，不能声称全文检索。
- 正文层预选最多4个不重叠的完整片段，连同来源开销不超过约1,400估算tokens。最终回复还有原有总上下文预算，超出时整块移除，避免裁掉否定或限制条件。指标为预选阶段估算，不冒充生成模型实际账单tokens。
- `start/end/sha256/url`保留原文溯源。来源片段和检索评分均不代表事实已经核实，角色片段也不等于原台词。
- 基础模型尚未校准：普通搜索采用模型排序与词法保留项；角色语气证据要求更高，低于固定开发阈值1.5不注入，无合适内容回到卡内示例，不拿视频作者说明充当角色说话方式。
- 模型不可用时明确标注`lexical_fallback`，不伪报Laya执行。正文不可读时普通搜索降为`search_snippet_only`。异常/降级缓存60秒，成功缓存24小时；版本、模型revision、用途、搜索源进入缓存键。
- 正文读取独立验证DNS、实际连接地址及每一次重定向，拒绝内网/IP混合解析和非常规端口，不携带账号cookie或代理凭据；不执行脚本，不绕过验证码。

## 实测与限制

2026-10-18，本机网络、同一“SillyTavern 官方文档”查询经SearXNG：百度CAPTCHA，Google access denied，Bing空结果，Yahoo 7条结果。此前Bing HTML可访问，但中文天气查询相关性较弱。这是该环境的实测，不代表这些服务永久不可用；`WEB_SEARCH_ENGINES`可配置，失败源原样记录，不绕过限制。

模型：`convaiinnovations/laya-multilingual`，revision `1720e3e3357cfe1e281542e223f8273b0890ca34`，权重SHA256 `9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204`。独立Mac MPS worker，未后训练，未改动原Router/Memory/Executor模型。

32条人为编写的开发探针（标题/正文各16条，只有4条正例；两阶段共享题材，不能当独立测试集）。固定分界1.5：

|判断|TP|FP|FN|precision|recall|
|---|---:|---:|---:|---:|---:|
|Laya标题|2|0|2|1.00|0.50|
|Laya正文|1|0|3|1.00|0.25|
|词法基线，两阶段相同输入|3|5|1|0.375|0.75|

样本过小，precision=1不能证明上线精度；基础Laya偏保守，角色语气题排序也存在明显错误。当前仅适合实验排序，不能宣称已完成高质量Jev筛选。下一步应收集真实网页、同角色不同人物发言、百科背景、无转录视频、时效错配和注入样本，按查询/来源分组隔离训练评测，再比较专项后训练Laya与Qwen4B。暂未开始后训练。

第一次真实文档检索：7候选页→尝试3页→读到3页→20候选chunk→4片段；已读取正文估算4,905 tokens，预选正文847 tokens（约减少83%，不含来源/提示词开销）。标题批约1.01秒、正文批约1.60秒，读取筛选约3.69秒，不含搜索源等待和回复生成。这是单次样例，不是p95或证据召回率。

角色检索实测读取到视频作者说明，未得到文字台词；因此不能宣称成功获取角色原话，严格语气接受策略回到卡内示例。它证明“搜到页面”与“有可用语气证据”必须区分。

## 部署与复现

可选搜索服务（新环境运行一次；已有18095服务时不要重复启动）：

```bash
python scripts/prepare_web_search.py
docker compose --env-file data/web-search/compose.env -f compose.search.yml up -d
```

生成目录在忽略规则内，私有secret不入库。开源服务使用官方固定镜像，未改SearXNG源码。JSON API在生成的settings.yml开启。

在独立虚拟环境安装`agent-runtime[web-filter]`，避免聊天Runtime加载Torch。下载固定revision的模型及`encoder/`配置与tokenizer（约644MB权重），将`WEB_FILTER_SERVICE_KEY`设置为随机私钥后启动：

```bash
HF_HUB_OFFLINE=1 python -m psych_agent_runtime.laya_web_service \
  --model-path /absolute/path/to/laya-multilingual --device mps --port 18106
```

Linux/无MPS改用`--device cpu`或实际可用的`cuda`。模型下载须在离线启动前完成。运行时配置：

```dotenv
WEB_SEARCH_ENABLED=true
WEB_SEARCH_USERS=your-preview-account
WEB_SEARCH_PROACTIVE_ENABLED=true
WEB_ROLE_VOICE_ENABLED=false
WEB_SEARCH_PROVIDER=searxng
WEB_SEARCH_BASE_URL=http://127.0.0.1:18095
WEB_SEARCH_ENGINES=360search,yandex
WEB_SEARCH_FALLBACK_ENGINES=naver,mwmbl
WEB_CONTEXT_ENABLED=true
WEB_FILTER_URL=http://127.0.0.1:18106
# 与worker相同的随机私钥，仅保存在私有环境文件
WEB_FILTER_SERVICE_KEY=
```

运行开发探针：在agent-runtime目录中执行`python -m training.evaluate_web_filter --output /private/path/base-probe.json`。API密钥只从环境读取，报告不含密钥。

前端工具详情显示真实候选页/读取页/预选片段数和模型/兜底状态；Runtime的`/v1/runtime/info`模型配置状态不能替代实际推理成功回执。功能默认关闭，维持本地白名单预览，不推正式环境。

参考：[SearXNG JSON API](https://docs.searxng.org/dev/search_api.html)、[官方Docker部署](https://docs.searxng.org/admin/installation-docker.html)、[GPT Researcher Context Filter](https://docs.gptr.dev/docs/gpt-researcher/gptr/context-filter)、[Trafilatura](https://github.com/adbar/trafilatura)。
