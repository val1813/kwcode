# Changelog

All notable changes to KWCode are documented here.

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
