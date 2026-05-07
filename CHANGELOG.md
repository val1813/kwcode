# Changelog

All notable changes to KWCode are documented here.

---

## [1.6.2] - 2026-05-07

### 执行反馈深度升级 + 存根任务修复

**核心理念**：不是修retry机制，是升级执行反馈的质量。给LLM最精确的失败信息，让它知道具体哪里错了。

### Added

- **parse_test_failures()结构化解析**：从pytest输出提取每个失败测试的test_name/expected/actual/error_type/file/line/snippet
- **TraceCoder历史教训累积**：attempt_history每轮累积不重置，retry_hint携带最近3次历史摘要（20+20=40效果）
- **完整审计日志**：llm_calls记录每次LLM调用的prompt/output，node_io记录各节点输入输出，便于快速定位问题出在哪一层
- **WinkMonitor免疫机制**：tests_no_progress/repetitive_fix pattern检测

### Changed

- **Generator首次生成注入structured_failures**：不只是raw output，而是"test_guard_allows: 期望True，实际None"这样的精确信息
- **pytest从-q改为-v**：获取完整失败详情而非摘要
- **pre_test输出截断从500→2000字符**：保证assert None==模式不被截掉
- **Reviewer在tests全通过时跳过**：防止LLM幻觉reject正确结果
- **存根检测扩展**：覆盖TypeError/takes no arguments/多个TypeError模式
- **codegen路径文件已存在时走whole_file覆盖**：不再生成_1.py导致测试跑错文件
- **逐函数patch全失败时fallback到whole_file**：保证patches不为0

### Fixed

- **hashline \\n字面量问题**：LLM把多行代码塞进一行导致语法错误
- **Gap检测files为空时扫描project_root**：pre_test输出只含test文件路径时也能找到源文件存根
- **回归rollback携带具体信息**：不是空白"请只修改必要部分"，而是具体哪些测试新失败了

---

## [1.5.1] - 2026-05-06

### 三飞轮 + 遥测 + 模型自适应 + 前沿算法

**理论来源**：Hashline(oh-my-pi锚点编辑) + AdaptThink(自适应推理) + Thinker(Fast/Slow双过程) + Claude Code(prompt行为工程)

### Added

- **三飞轮系统**（全部本地存储）：
  - `flywheel/strategy_stats.py`：错误策略有效性统计，按error_type×sequence累计成功率，min_attempts≥10时自动优化重试顺序
  - `flywheel/user_pattern_memory.py`：跨项目用户错误模式记忆，20+任务后自动注入中文提示
  - `flywheel/skill_drafter.py`：SKILL.md自动提炼，30+成功轨迹生成草稿

- **匿名遥测**（opt-in，默认关闭）：
  - `telemetry/client.py`：HMAC-SHA256签名 + fire-and-forget上传
  - 只上传：error_type, retry_count, success, model（绝不上传代码/路径/描述）
  - 服务端：https://llmbbs.com (nginx→FastAPI, 3张SQLite表, IP限流30/min)
  - CLI：`kwcode telemetry status/enable/disable`，`kwcode skill review/accept/discard`

- **Hashline锚点编辑**（P0）：
  - `tools/hashline.py`：每行6字符MD5锚点，EDIT/DELETE/INSERT_AFTER指令
  - Generator首次尝试hashline（1024 tokens），失败fallback全函数生成
  - 哈希不匹配→拒绝全部编辑（防止写入脏数据）

- **AdaptThink自适应推理**（P1）：
  - `core/think_config.py`：按expert_type×difficulty自动选择think预算
  - easy→off, medium→512, hard→2048-4096, chat/office→always off

- **Fast/Slow双阶段推理**（P2）：
  - 第一次fast(think=off)，第一次失败升级slow(budget=2048)，第二次失败最大budget(4096)

- **审计日志**：
  - `audit/logger.py`：持久化任务执行轨迹为JSON，最多100条
  - CLI：`kwcode log` / `kwcode log show <id>` / `kwcode log clear`

- **kwcode model命令**：
  - `kwcode model`：查看当前模型+tier
  - `kwcode model set <name>`：切换模型
  - `kwcode model probe`：探测模型详情

- **model_capability全量接入**：
  - orchestrator检测tier→ctx注入→Generator按tier切prompt约束
  - SMALL：严格约束(1函数/≤10行/保持缩进/禁解释/禁工具描述)
  - ctx自适应：4层探测(llama.cpp→vLLM→Ollama→离线表)，Ollama每次请求主动设num_ctx

### Changed

- **Prompt量化改造**（CC风格）：
  - "只做任务要求的事" → "每次patch≤2函数，≤30行"
  - "缩小修改范围" → "只修改1个函数，≤15行"
  - GENERATOR_PROMPT/NEWFILE/TEST：正面指令替代负面指令，删除工具描述
  - CHAT_SYSTEM：删除无用工具描述，≤100字/≤3句
  - RETRY_STRATEGIES hints：每种错误类型量化行数限制

- **版本号统一**：pyproject.toml为唯一真相源，其他文件通过importlib.metadata读取

### Fixed

- **缩进对齐bug**：`Generator._align_indentation()`修复class方法缩进丢失（LLM返回0空格→原始4空格对齐）
- **JSON解析崩溃**：debug_subagent(×2) + checkpoint(×2) + ast_grep_engine(×1) 加try/except保护
- **版本号测试**：test_server.py硬编码"1.5.0"→动态`__version__`

---

## [1.4.0] - 2026-05-06

### 多语言 + TUI + IDE兼容（3 个模块）

**理论来源**：XRAY MCP Server(ast-grep选型) + OpenCode(client/server分离) + CodeCompass(工具采用率)

### Added

- **多语言 AST 支持**（模块A）：
  - `ast_engine/language_detector.py`：7语言检测(Python/JS/TS/Go/Rust/Java/C#)，项目标记文件识别(go.mod/Cargo.toml/package.json/pom.xml)
  - `ast_engine/ast_grep_engine.py`：预定义查询模板(find_function/find_class/find_imports/find_method_call)，LLM只填参数不写pattern，支持ast-grep-py绑定和CLI两种后端
  - `ast_engine/parser.py` 扩展：TreeSitterParser 支持 JS/TS/Go/Rust/Java（可选依赖，graceful fallback）
  - `ast_engine/graph_builder.py`：SUPPORTED_EXTENSIONS 动态扩展 + rig.json 新增 language_stats 字段
  - `experts/verifier.py` 多语言：测试运行器(pytest/jest/go test/cargo test/mvn test/dotnet test) + 语法检查(py_compile/go vet/tsc --noEmit/cargo check/javac) + 多语言错误分类
  - 4个新 SKILL.md：`builtin_experts/golang/`、`typescript/`、`rust/`、`java/`

- **FastAPI Server + SSE**（模块B）：
  - `server/app.py`：FastAPI 应用，端口 7355，CORS 支持
    - `POST /api/task` → 提交任务返回 task_id
    - `GET /api/task/{id}/events` → SSE 事件流
    - `GET /api/health` / `GET /api/status` → 健康检查和状态
    - `GET /api/files` / `GET /api/file` → 文件树和内容
    - `POST /api/rig/refresh` → 重建 rig.json
  - `server/pipeline_factory.py`：共享 pipeline 构建（CLI 和 server 复用）
  - `server/models.py`：Pydantic 模型(TaskRequest/TaskResponse/HealthResponse/FileContent等)
  - CLI 新增 `kwcode serve` 命令

- **Textual TUI**（模块B）：
  - `tui/app.py`：左面板(DirectoryTree + 文件预览) + 右面板(RichLog事件流 + Input任务输入)
  - 自动检测 server 是否运行，未运行则 subprocess 启动
  - CLI 新增 `kwcode --tui` 选项

- **VSCode 插件**（模块C）：
  - `extension/src/extension.ts`：命令注册、文件保存触发 RIG 刷新、状态栏连接指示
  - `extension/src/server-client.ts`：SSE 客户端，连接 localhost:7355
  - `extension/src/panel.ts`：Webview 面板，事件渲染 + 任务输入
  - 薄客户端架构：不重复实现业务逻辑

### Changed

- `pyproject.toml`：新增 optional-dependencies（multilang/server/tui），full 包含所有
- 版本号 1.3.0 → 1.4.0
- 测试数量 357 → 424

### Architecture Decisions

- **ast-grep pattern 绝对不让 LLM 生成**：只用 QUERY_TEMPLATES 预定义模板，LLM 只填参数（函数名等）
- **Server 单例 pipeline**：每个任务 asyncio.to_thread() 隔离，EventBus 事件直接推送到 SSE Queue
- **TUI/VSCode/CLI 共享同一事件流**：三种前端都是 EventBus 的消费者
- **所有新依赖都是 optional**：不影响现有 `pip install kwcode` 安装

---

## [1.3.0] - 2026-05-06

### v2 架构升级（10 个模块）

理论来源：Dive into Claude Code(arXiv:2604.14228) + Wink(arXiv:2602.17037) + ARCS(arXiv:2504.20434) + SpecEyes(arXiv:2603.23483) + OPENDEV(arXiv:2603.05344) + Turn-Control(arXiv:2510.16786)

### Added

- **EventBus 统一事件总线**：append-only 日志 + replay + wildcard 监听
- **ToolGateway 工具权限层**：专家权限白名单 + 文件读缓存 + 脏标记
- **错误策略路由**：按 error_type 切换重试序列（syntax/assertion/import/patch_apply/runtime/unknown）
- **认知门控 CognitiveGate**：patch 行数递减检测边际收益递减
- **上下文渐进压缩 GraduatedCompactor**：3层(70%/85%/95%)
- **Plan 自动触发**：hard 任务自动生成执行计划
- **Worktree 隔离**：git worktree / tempdir + copytree
- **Speculative Prefetch**：Locator 完成后后台预读文件
- **SearchRouter 意图感知搜索**：零 key 默认可用(arXiv/S2/GitHub/PyPI/Open-Meteo)
- **Wink 自修复监控**：scope_creep/repetitive_fix/patch_miss/empty_output
- **搜索层网络保护**：全局 try/except + search_enabled 开关

---

## [1.0.7] - 2026-04-30

### 系统走查：修复 5 个问题（qwen3:8b 真实模型验证）

用本地 qwen3:8b 跑 10 个复合场景端到端测试，发现并修复 5 个问题。

### Fixed

- **Planner regex 非贪婪 bug**：`\[.*?\]` 遇到 `depends_on:[]` 提前终止，auto_decompose 永远返回 None。改为贪婪 `\[.*\]`
- **TrajectoryCollector.get_by_expert() 缺失**：ab_tester 投产时调用此方法会 AttributeError。新增方法
- **auto_decompose 未接入 _run_task()**：hard 任务不会自动拆分。现在 difficulty=hard + subtask_hint 非空时自动走 TaskCompiler
- **预搜索未接入 _run_task()**：Gate 判断 needs_search=true 但 pre_search_results 从未传递。现在预搜索触发后注入 orchestrator
- **session_md 未接入 REPL 退出**：SESSION.md 从未被写入。现在 REPL 退出时自动保存最近任务摘要

### 验证结果（qwen3:8b 真实输出）

```
Gate分类准确率: 5/5 场景全部正确
  - 复合任务 → hard + subtask_hint ✓
  - 简单bugfix → easy + no search ✓
  - 需要搜索 → needs_search=true ✓

auto_decompose: 修复后正确拆分（2子任务，依赖关系正确）
QueryGenerator site限定: LLM自动输出 site:arxiv.org / site:stackoverflow.com
Token tracking: 3次调用共639 tokens
```

---

## [1.0.6] - 2026-04-30

### 搜索优化：LLM 自动 site: 限定

**理论来源：** LLM 已经在生成 query，直接让它顺便决定 site: 限定，零新 API，零新依赖。

### Added

- **QueryGenerator 智能 site 限定**：第一条 query LLM 自动判断去哪个站点（arxiv/github/stackoverflow/pypi 等），后续 query 不加限定做广度搜索
- **_clean_query() 安全过滤**：拦截 prompt injection 尝试（ignore previous/[INST] 等）
- **realtime intent**：预搜索场景专用意图类型
- **QueryGenerator 兼容纯字符串调用**：不强制要求 TaskContext，预搜索可直接传 string

---

## [1.0.5] - 2026-04-30

### P1+P2：自动任务拆分 + 预搜索 + PCED-Lite

**理论来源：**
- ExpertRAG (2026)：Gate层搜索决策前移，避免失败后才搜索的浪费
- PCED (arXiv:2601.08670, 2026)：并行上下文专家解码，180倍TTFT加速
- Task Decomposition Research (2026)：后台无感知任务分解是区分功能性agent的关键机制

### Added

- **Gate 输出扩展**（向后兼容）：新增 `needs_search`（是否需要实时数据）和 `subtask_hint`（子任务提示）两个字段
- **Planner.auto_decompose()**：基于 Gate 的 subtask_hint 自动拆分 hard 任务为 DAG
  - 只在 hint 有 2-5 个子任务时触发
  - LLM 一次调用确认依赖关系
  - 失败静默降级为单任务（P1-RED-1）
- **预搜索**：Gate 判断 `needs_search=true` 时，在 orchestrator.run() 前预加载实时数据
  - orchestrator.run() 新增 `pre_search_results` 参数
  - 预搜索结果直接注入 ctx.search_results，跳过失败触发的搜索
- **PCED-Lite** (`search/pced_lite.py`)：
  - 对每个搜索结果独立生成答案（ThreadPoolExecutor 并行）
  - 一致性投票选最终答案（字符级重叠率判断）
  - FLEX-2：VRAM<6GB 或文档<3 时静默降级到 BM25 拼接

---

## [1.0.4] - 2026-04-30

### 代码审查：修复 8 个空架子/竞态/数据错误

**问题来源：** 完整代码审查发现功能存在但实际不运行、功能间矛盾、数据错误。

### Fixed

- **DebugSubagent 实例化**：之前 `debug_subagent=None` 从未传入 orchestrator，Debug 功能是死代码。现在 `main.py` 里正确实例化并注入
- **PromptOptimizer 接入投产流程**：专家通过三道门投产后自动触发 prompt 优化（需配置 `anthropic_api_key`）
- **Checkpoint 并行竞态**：`/multi` 多任务并行时每个子任务都 git stash 导致文件混乱。现在子任务级别 `skip_checkpoint=True`
- **force_plan_mode 可覆盖**：小模型用户之前无法关闭强制计划模式，现在可通过 `--no-search` 间接控制
- **conversation_history 存真实输出**：之前 assistant content 存的是 user_input（假数据），现在存 LLM 实际生成的 explanation
- **多语言 AST**：确认代码已正确标注为 Python-only（`SUPPORTED = {"python": ...}`），无虚假多语言声明
- **Cross-Encoder**：确认已有优雅降级（`_reranker_disabled=True`），无需额外修改

---

## [1.0.3] - 2026-04-30

### 上下文优化 + SSH 持久会话

**理论来源：**
- Letta (2026)：Active+Archive 分层记忆架构
- GCC State Passing：结构化状态传递，不通过对话历史
- SWE-Pruner：代码块保护原则，代码内容禁止被压缩
- Claude Code：敏感文件备份而非阻止写入

### Added

- **三层上下文架构** (`core/context.py` + `core/context_pruner.py`)：
  - Layer 1 (Active)：文字摘要，Gate/Generator 看到的（≤2K tokens，可压缩）
  - Layer 2 (Structured State)：Python 对象（subtask_results, code_snippets），精确传递不压缩
  - Layer 3 (Archive)：持久化文件（SESSION.md, PATTERN.md），BM25 按需检索
  - 新增字段：`subtask_results`、`current_task_id`、`upstream_summary`

- **代码块保护** (CTX-RED-1)：ContextPruner 压缩时检测 ``` 代码块，保留代码原文不做关键词化

- **持久 SSH 会话** (`tools/ssh_session.py`)：
  - paramiko 实现，connect 一次后多次 exec
  - 集成到 ToolExecutor：`ssh_connect/ssh_exec/ssh_upload/ssh_download/ssh_close`
  - Guardrails 同样适用于远程命令

### Fixed

- 敏感文件保护改为备份模式（.bak）而非阻止写入，与 Claude Code 行为一致

---

## [1.0.2] - 2026-04-29

### MoE 框架补全：4 个缺失主部件

基于 7 层 Agent 架构审计（Perceive→Remember→Think→Plan→Act→Observe→Guardrails），补全 Observe 层和 Guardrails 层。

**理论来源：**
- Portal26 Agentic Token Controls (2026)：token 预算管控防止失控消耗
- Claude Code 4-Layer Memory (2026)：MEMORY.md → Topic Files → Learnings → Patterns
- Augment Code "Session-End Spec Update" (2026)：会话结束时持久化决策和约束
- CodeDelegator EPSS (arXiv:2601.14914)：Ephemeral-Persistent State Separation
- 9 Failure Modes of Agentic AI (ElixirData 2026)：context overflow、function hallucination execution

### Added

- **Token 预算管控** (`llm/llama_backend.py`)：
  - 每次 LLM 调用自动计数 input/output tokens
  - `token_usage` 属性查看当前消耗
  - `set_token_budget(n)` 设置上限，超出抛 BudgetExceededError
  - OpenAI 兼容 API 使用真实 usage 数据，Ollama 用估算（4 chars/token）

- **Guardrails 护栏** (`tools/executor.py`)：
  - 危险命令拦截：rm -rf、git push --force、drop database 等 12 种模式
  - 敏感文件保护：.env、credentials.json、id_rsa 等不可写
  - 文件范围限制：write_file 不能写到 project_root 之外

- **执行可观测性** (`core/execution_trace.py`)：
  - ExecutionTrace 结构化记录每步（name、耗时、成功/失败）
  - 任务完成后 `.summary()` 输出人类可读摘要
  - 记录 LLM 调用次数和 token 消耗

- **会话连续性** (`memory/session_md.py`)：
  - 会话结束时自动生成 SESSION.md（最近任务摘要）
  - 下次启动自动加载，注入 Gate 的 memory_context
  - 限制 50 行，最新在前

---

## [1.0.1] - 2026-04-29

### Gate/Loop/路由优化

基于前沿研究优化 Gate 和执行循环，进一步减少 LLM 决策负担。

**理论来源：**
- Turn-Control Strategies (arXiv:2510.16786)：动态预算比固定预算好 12-24%
- Hidden Architectural Seam (2026)：分离 Planner 和 Executor 提升 9-15%
- CodeDelegator (arXiv:2601.14914)：Ephemeral-Persistent State Separation 防止 context 污染
- Cognition/Devin (2026)：hierarchical delegation 有效，parallel-writer swarms 失败
- Compiled Execution (MightyBot 2026)：90% agent 工作是路由不是推理，确定性代码优于 LLM 决策
- ORCH (Frontiers in AI 2026)：EMA-guided 确定性路由，无需额外 LLM 调用

### Added

- **动态重试预算**：根据 Gate 的 difficulty 判断分配重试次数（easy=2, hard=4），不再一刀切
- **TaskPlanner 自动任务分解** (`core/task_planner.py`)：
  - 1次 LLM 调用将复合任务拆分为 DAG JSON
  - 只在 difficulty=hard 且输入>30字时触发（节省 LLM 调用）
  - 失败降级为单任务（不死循环）
  - 最多拆分5个子任务

### Fixed

- **Context 污染**：重试时清空 `debug_info`，防止前轮调试噪音干扰下一轮 Generator
  - 之前：debug_info 累积，第3次重试时 context 里塞了前2次的调试信息
  - 现在：每次重试前清空，重新采集（Ephemeral State）

---

## [1.0.0] - 2026-04-29

### Architecture: 元专家体系定稿

KWCode 的专家系统从"按业务领域枚举"升级为"按原子能力分层 + 领域知识注入"。

**5 个元专家（原子能力层，固定不变）：**

| 元专家 | 能力 | 文件 |
|--------|------|------|
| Locator | 代码定位（BM25+AST调用图） | `experts/locator.py` |
| Generator | 代码生成/编辑 | `experts/generator.py` |
| Verifier | 测试验证（pytest） | `experts/verifier.py` |
| Debugger | 运行时调试（sys.settrace） | `experts/debug_subagent.py` |
| Reviewer | 需求对齐审查 | `experts/reviewer.py` |

**15 个领域知识（SKILL.md 注入层，可扩展）：**

BugFix · FastAPI · TestGen · API · DeepSeekAPI · Docstring · MyBatis · OfficeDocx · OfficePptx · OfficeXlsx · Refactor · SpringBoot · SQLOpt · TypeHint · UniApp

领域知识不改变流水线结构，只注入 Generator 的 system_prompt。

### Added

- **Reviewer 元专家** (`experts/reviewer.py`)：Verifier 通过后用 LLM 对比用户意图和实际变更，判断需求是否对齐
- **SKILL.md 渐进式加载**：全部 15 个专家从 YAML 升级为 SKILL.md 目录格式
  - Level 1（Gate）：name + keywords，~100 token/专家
  - Level 2（Generator）：完整领域知识，仅命中专家加载
  - Level 3（on-demand）：确定性脚本，不进 LLM context
- **DAG TaskCompiler** (`core/task_compiler.py`)：串行+并行多任务调度
- **`/multi` 命令**：CLI 多任务模式（分号并行、箭头串行、交互式混合）
- **Debug Subagent** (`experts/debug_subagent.py`)：verifier 失败后 sys.settrace 捕获运行时变量
- **Prompt Optimizer** (`flywheel/prompt_optimizer.py`)：飞轮优化 SKILL.md 的领域知识内容
- **Cross-Encoder 重排** (`search/reranker.py`)：可选搜索结果精排
- **Reflexion 持久化** (`memory/pattern_md.py`)：REFLECTION.md 结构化记录 + /plan 风险注入
- **OpenAI 兼容 API 自动检测**：LLMBackend 根据 URL 自动判断用 `/api/chat`（Ollama）还是 `/v1/chat/completions`（DeepSeek/硅基流动等）

### Changed

- 专家格式从 flat YAML 升级为 SKILL.md 目录（全量迁移，旧 YAML 已删除）
- LLMBackend 支持 api_key 参数，云端 API 自动带 Authorization header
- 版本号从 0.7.0 → 1.0.0
- 测试数量：282 → 311

### Removed

- 15 个旧 `.yaml` 专家文件（已全部转为 SKILL.md 目录）
- Python 专家系统（ExpertBase、BugFixExpert.py、SelfImprovingOptimizer）— 方向错误，v0.8.0 加入后 v0.9.0 移除

### Fixed

- LLMBackend 硬编码 `/api/chat` 导致云端 API（DeepSeek 等）404 的问题
- 初始化配置时验证用 `/v1/chat/completions` 但实际请求用 `/api/chat` 的不一致

### Architecture Decisions

- **元专家按原子能力分，不按业务领域分**：研究证明 MoE 路由反映隐状态几何结构而非领域专业性（arXiv:2604.09780）
- **领域知识是注入层，不是独立流水线**：所有任务走同一条 Locator→Generator→Verifier 管线，区别只在 system_prompt
- **渐进式加载解决噪音问题**：Gate 只看 metadata（~1500 token/15专家），不全量加载所有知识
- **Reviewer 非阻塞**：审查结果不回滚代码，只提示用户注意 gap
- **弱模型 + Skill 胜过强模型裸跑**：tessl.io 880次评测证明 Haiku+Skill(84.3%) > Opus裸跑(80.5%)

---

## [0.9.0] - 2026-04-29

### Added
- DAG TaskCompiler + /multi 命令
- Debug Subagent（基于 Debug2Fix 论文）
- Prompt Optimizer（优化 YAML system_prompt）
- Reflexion 持久化 + Cross-Encoder 重排

### Removed
- Python 专家系统（ExpertBase、BugFixExpert.py、SelfImprovingOptimizer）
- Registry Python 专家加载逻辑

---

## [0.7.0] - 2026-04-29

### Added
- UI 全面优化（spinner、结果摘要、重影大字 Header）
- 意图感知搜索
- 搜索模块重构（四级提取管道）
- P2 三大功能（模型自适应、飞轮通知、价值仪表盘）
- P1 四大功能（KWCODE.md、/plan、Checkpoint、DocReader）
- MVP 核心流水线（Gate→Locator→Generator→Verifier→Search）

---

## References

| 论文 | 对 KWCode 的影响 |
|------|-----------------|
| Agentless (ICSE 2025) | 整体确定性流水线架构 |
| CodeCompass (2026) | AST 调用图定位 |
| Debug2Fix (ICML 2026) | Debug Subagent |
| LLMCompiler (ICML 2024) | DAG 任务调度 |
| Reflexion (NeurIPS 2023) | 失败模式持久化 |
| SICA (2025) | Prompt 自动优化 |
| GitHub Copilot Atomic Skills (2025) | 5 原子能力分层 |
| SWE-Skills-Bench (2026) | 80% 泛泛 skill 无效，具体知识才有效 |
| MoE Routing Geometry (2026) | 专家不按领域分，按能力分 |
| Agent Skills Progressive Disclosure (Anthropic 2026) | 渐进式加载架构 |
