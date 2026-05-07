# KWCode Project Status

> Path: D:\program\codeagent2604\kwcode
> GitHub: https://github.com/val1813/kwcode
> Started: 2026-04-26
> Goal: Local-model coding agent — maximize task completion rate via deterministic expert pipeline

---

## Current: v1.6.2 (2026-05-07)

513 tests green + 67个bench tasks + 62个专项诊断测试。
执行反馈深度升级：结构化测试失败解析 + TraceCoder历史教训累积 + whole_file写入修复 + 完整审计日志。

### v1.6.2 — Execution Feedback Depth Upgrade

**核心理念：不是修retry机制，是升级执行反馈的质量。**

- `parse_test_failures()`: 从pytest输出提取每个失败测试的expected/actual/error_type/snippet
- TraceCoder `attempt_history`: 每轮累积不重置，retry_hint携带最近3次历史摘要
- 完整审计日志: `llm_calls`记录每次LLM调用prompt/output，`node_io`记录各节点IO
- pytest从-q改为-v: 获取完整失败详情
- 存根检测扩展: TypeError/takes no arguments/多个TypeError
- codegen路径文件已存在时走whole_file覆盖（不再生成_1.py）
- 逐函数patch全失败时fallback到whole_file（保证patches不为0）
- Reviewer在tests全通过时跳过（防LLM幻觉reject）

### v1.6.1 — Architecture Convergence (ExpertDirective收敛)

**核心变更：删除独立Expert类，统一走pipeline**

- 删除 `experts/whole_file_impl.py`（238行）和 `experts/dependency_fix.py`（99行）
- 存根实现：Generator通过ctx.gap自动解除函数限制（scope=whole_file），不需要独立Expert
- 依赖安装：EnvProber在Phase0处理，不需要独立Expert
- `_select_moe_expert()`始终返回None，所有任务走统一pipeline
- GAP_TO_EXPERT_TYPE映射：NOT_IMPLEMENTED/STUB_RETURNS_NONE/MISSING_DEP → locator_repair
- Gate VALID_EXPERT_TYPES移除whole_file_impl和dependency_fix

**Generator增强**
1. `_build_system()`注入upstream_constraints到system prompt（之前只在prompt层注入，system层缺失）
2. `_build_retry_hint()`携带上次生成的代码前300字符，LLM能看到自己的错误避免重复
3. tier=small分支增加填空式编写规范（函数签名不变/只替换函数体/每个TODO 3-5行）

**测试同步更新**
- test_stub_ratio_threshold.py：移除WholeFileImplExpert.can_handle()测试，改为验证GAP_TO_EXPERT_TYPE映射
- test_routing_layer_stats.py：expected_expert从whole_file_impl/dependency_fix改为locator_repair

**设计意图：bench结果可直接归因于GapDetector+scope+ExecutionStateTracker+EnvProber这套纯确定性机制，不被枚举专家类干扰判断。**

### v1.6.0 — MoE Deterministic Architecture

**核心原则：LLM只做代码生成，所有路由/决策/状态判断全部确定性化。**

**GapDetector** (`core/gap_detector.py`)
- GapType enum（11种）：NONE/NOT_IMPLEMENTED/STUB_RETURNS_NONE/LOGIC_ERROR/MISSING_DEP/SYNTAX_STRUCTURAL/MISSING_TOOLCHAIN/WRONG_FILE/NO_TEST/ENVIRONMENT/UNKNOWN
- Gap dataclass：gap_type + confidence + files + functions + error_msg + suggestion
- GapDetector.compute()：纯正则匹配，零LLM调用，按优先级分类
- GAP_TO_EXPERT_TYPE：确定性映射 GapType → expert_type（v1.6.1统一为locator_repair）

**ExecutionStateTracker** (`core/execution_state.py`)
- TestDelta dataclass：每次修改后的测试状态变化
- set_baseline() → record() → has_regression() → get_best_partial_state()
- Git bisect式定位：知道哪步引入了问题，不盲目reset
- 代码状态回滚交给Checkpoint，本类只追踪测试状态

**EnvProber** (`core/env_prober.py`)
- 任务开始前确定性探测并修复环境（工具链+依赖+rig.json预构建）
- LANG_TOOLCHAIN dict：6种语言的check/install/dep_cmd/dep_file
- 缓存.kaiwu/env_profile.json（24h TTL，只缓存成功）
- _find_working_test_cmd()：go用build验证（spec v2修正）

**WholeFileImplExpert** — 已删除（v1.6.1），功能由Generator通过ctx.gap scope=whole_file处理

**DependencyFixExpert** — 已删除（v1.6.1），功能由EnvProber在Phase0处理

**Gate重构** (`core/gate.py`)
- 确定性优先路由，LLM只做最后兜底二分类
- 优先级：特殊任务关键词 → Gap路由(conf>=0.7) → 关键词匹配 → LLM兜底
- _resolve_intent_vs_gap()：三层置信度消解（>=0.85 gap wins / 0.5-0.85 intersection / <0.5 user wins）
- routing_source字段：记录每次路由决策来源

**Orchestrator重构** (`core/orchestrator.py`)
- Phase 0：EnvProber.probe_and_fix()（确定性环境修复）
- Phase 1：无条件pre_test → GapDetector → ExecutionStateTracker.set_baseline()
- Phase 2：Gap驱动expert_type覆盖（确定性优先于LLM分类）
- Retry loop增强：回归检测→checkpoint.restore() / env_changed→_recompute_gap()
- _select_moe_expert()：v1.6.1起始终返回None（统一走pipeline）

**Verifier增强** (`experts/verifier.py`)
- whole_file write_mode支持（直接写入整个文件）
- _detect_wrong_file()：确定性检测修改文件与报错文件不匹配

**审计日志增强** (`audit/logger.py`)
- 目录分离：成功→~/.kaiwu/logs/success/ / 失败→~/.kaiwu/logs/failed/
- 新增字段：routing_source, initial_gap_type, iterations[]
- log_iteration()：每轮retry记录gap_type/expert_selected/can_handle_results/transition_reason/test_delta
- 向后兼容：list_logs()/show_log()同时扫描新旧目录

**TestParser** (`core/test_parser.py`)
- extract_failing_tests() / extract_passing_tests()
- 纯正则，支持pytest/go/jest/rust四种格式
- 供GapDetector、ExecutionStateTracker、Orchestrator共用

**TaskContext新增字段** (`core/context.py`)
- gap: Gap dataclass instance
- confirmed_test_cmd: EnvProber提供的已验证测试命令
- routing_source: 路由来源审计字段

**专项诊断测试** (`tests/diagnostic/`, 62个测试)
- test_gap_detector_accuracy.py：20个手工样本，GapType分类准确率>90%
- test_stub_ratio_threshold.py：10+10文件，stub_ratio阈值验证>85% + GAP映射验证
- test_execution_tracker_value.py：5个多迭代场景，回归检测+最优中间状态
- test_routing_layer_stats.py：三层消噪触发率验证，gap_detector>llm_fallback

**三飞轮系统（全部本地）**
- `flywheel/strategy_stats.py` — 错误策略有效性统计（~/.kwcode/strategy_stats.json）
  - record(error_type, sequence, success, retries) → 按error_type×sequence累计成功率
  - get_best_sequence() → min_attempts≥10时返回最优策略，否则用默认
  - 集成到orchestrator._record_flywheel()，每次任务完成后自动记录
- `flywheel/user_pattern_memory.py` — 跨项目用户错误模式（~/.kaiwu/user_patterns.json）
  - record_task() → 统计error_type频率+成功率（滑动平均）
  - get_warning_hint() → 20+任务后生成中文提示注入ctx.kaiwu_memory
  - 5种错误类型各有针对性提示（syntax/assertion/import/runtime/patch_apply）
- `flywheel/skill_drafter.py` — SKILL.md自动提炼（.kaiwu/skill_draft.md）
  - 30+成功轨迹后自动生成策略草稿
  - CLI: `kwcode skill review/accept/discard`

**匿名遥测（opt-in，默认关闭）**
- `telemetry/client.py` — fire-and-forget daemon thread + httpx 3s超时
  - 只上传4字段：error_type, retry_count, success, model
  - 绝不上传：代码/路径/描述/用户身份
- `onboarding.py` — init时询问opt-in（Confirm.ask，default=False）
- CLI: `kwcode telemetry status/enable/disable`
- config: `telemetry_enabled` 顶层字段，缺失=关闭（向后兼容）

**服务端（已部署）**
- https://llmbbs.com → nginx反代 → 127.0.0.1:9753 (FastAPI+SQLite)
- 3张表：task_events / daily_aggregates / strategy_effectiveness
- API: POST /api/v1/event, GET /api/v1/health, GET /api/v1/stats
- systemd: kwcode-telemetry.service, auto-restart
- Let's Encrypt证书，自动续期

**CLI增强**
- `kwcode stats` 增强：展示三飞轮状态+遥测状态
- `kwcode telemetry status/enable/disable` — 遥测管理
- `kwcode skill review/accept/discard` — SKILL.md草稿管理

**集成点**
- orchestrator.__init__: +StrategyStats +UserPatternMemory +TelemetryClient
- orchestrator.run(): ctx创建后注入user_pattern warnings + ctx._errors_encountered追踪
- orchestrator._record_success/_record_failure_result: 调_record_flywheel()
- _record_flywheel(): 策略统计 + 用户模式 + 遥测，三路全非阻塞

**审计日志** (`audit/logger.py`)
- AuditLogger: start() → log(stage, detail) → write(ctx, elapsed, success, model)
- 存储：~/.kaiwu/logs/YYYY-MM-DD_HHMMSS_<expert_type>.json
- 不记录代码内容，只记录：任务描述/Gate分类/专家执行时间/文件名/测试结果/重试次数
- 最多保留100条，超出自动清理
- orchestrator._emit()从@staticmethod改为实例方法，每个事件自动记录到audit
- CLI: `kwcode log` / `kwcode log show <id>` / `kwcode log clear`

**kwcode model命令** (`cli/commands/model_cmd.py`)
- `kwcode model` — 显示当前模型配置+能力tier
- `kwcode model set <名称>` — 切换模型（写入config.yaml）
- `kwcode model probe` — 探测模型详情（Ollama API: family/参数量/量化/reasoning）

**缩进对齐修复** (`Generator._align_indentation`)
- 修复系统性bug：LLM返回class方法时丢失缩进（0空格 vs 原始4空格）
- apply_patch替换后方法"跑出"class导致IndentationError
- 修法：_generate_modified()返回后立刻调_align_indentation()补齐缩进差

**P0: Hashline锚点编辑** (`tools/hashline.py`)
- add_anchors(): 每行加6字符MD5哈希锚点 `行号|哈希| 内容`
- parse_anchor_edits(): 解析 EDIT/DELETE/INSERT_AFTER 指令
- apply_anchor_edits(): 验证哈希→应用编辑，任一哈希不匹配则拒绝全部
- Generator首次尝试用HASHLINE_PROMPT（max_tokens=1024），失败fallback到完整函数生成
- 效果：模型只输出编辑指令而非复现整个函数，减少输出token，消除patch_apply文本不匹配

**P1: Think模式自适应** (`core/think_config.py`)
- get_think_config(expert_type, difficulty) → {"think": bool, "budget": int}
- 策略表：easy→think=off / medium→budget=512 / hard→budget=2048-4096
- chat/office永远关闭think（不需要推理）
- Generator根据think_config调整max_tokens：base + budget
- orchestrator在Gate分类后自动设置ctx.think_config

**P2: Fast/Slow双阶段推理**（融入orchestrator retry loop）
- 第一次尝试：fast think（默认think=off，快速生成）
- 第一次失败：升级到slow think（budget=2048）
- 第二次失败：最大think预算（budget=4096）
- 与现有retry_strategy(0→1→2)正交，think_budget独立递增
- 对reasoning模型(QwQ-32B等)效果最明显

**测试：21个新测试（P0-P2）**
- Hashline: anchors/strip/parse/apply/mismatch/delete/insert/roundtrip (12)
- ThinkConfig: easy/hard/chat/unknown/apply_tokens (8)
- FastSlow: default/escalation (1)

**Reviewer闭环（关键架构修复）**
- 原问题：Reviewer发现"改错文件"只记日志，不触发重试，36任务假成功
- 修复：_record_success返回None → retry loop捕获 → 重置Generator/Verifier → 重试
- review gap注入ctx.retry_hint，Generator下次修正方向
- Reviewer prompt增强：注入initial_test_failure让LLM看到测试期望

**Verifier 0/0假成功消除**
- tests_total=0时检查是否有测试文件存在
- 有测试文件但0执行 → passed=False，报错"测试未执行"
- 消除benchmark 36/37任务"audit成功但bench失败"的根因

**Syntax熔断按tier区分**
- SMALL：1次重试后熔断（小模型重复同样错误）
- MEDIUM/LARGE：2次重试后熔断（32B第一次syntax error常是偶然）

**Test-First Loop（CC架构核心改动）**
- orchestrator.run()：locator_repair/refactor任务先调verifier.run_tests_only()拿测试报错
- locator.locate_from_test_error()：从pytest/go test报错提取File+行号，精准定位（不靠语义搜索猜）
- 优先级：test_error定位 > BM25+图 > LLM猜，test_error成功则跳过语义搜索
- 解决benchmark36/37任务"audit成功但bench失败"的根因：Verifier 0/0假成功

**Verifier修复（P0影响所有结果）**
- 测试文件发现：扫描project_root下`*_test.py`/`test_*.py`/`*_test.go`/`*.test.ts`，不只看tests/目录
- 找不到测试文件才返回0/0，有测试文件但没在tests/时用文件路径直接跑pytest
- 工具链检测：go/node/rust/java缺失时自动安装（apt-get），不再报syntax error
- Go语法检查：go: not found不报语法错误（返回None跳过）

**Locator增强**
- locate_from_test_error()：从Python/Go/TS测试报错提取文件名+行号+函数名
- 过滤test文件和stdlib，只返回业务代码文件
- import语句提取被测模块名，反向定位源文件

**Prompt约束量化改造（CC风格）**
- GENERATOR_BASE_SYSTEM：定性→量化
  - "只做任务要求的事" → "每次patch只修改≤2个函数，修改行数≤30行"
  - "不要动无关代码" → "不触碰报错行±20行范围外的无关代码"
  - "不要加注释" → "不添加任何import/类型注解/docstring/注释到未修改的代码"
- GENERATOR_PROMPT：
  - "只修改必要的部分" → "修改行数≤15行，不改动与错误无关的行"
- RETRY_STRATEGIES hints：
  - syntax: "不改其他逻辑" → "修改≤5行，不触碰其他函数"
  - assertion: "只改最小代码" → "只改1个函数，修改≤10行"
  - unknown: "缩小修改范围" → "只修改1个函数，修改≤15行"
- CHAT_SYSTEM：
  - "简短友好回复，2-3句话即可" → "≤100字回复，≤3句话"
- CHAT_SEARCH_FAIL_SYSTEM：
  - 整段重写为"回复≤50字"硬约束

**遥测防护（服务端三层守卫）**
- HMAC-SHA256签名：客户端用密钥签payload，无签名→403
- IP限流：同一IP每分钟≤30次，超限→429
- 字段校验：error_type枚举白名单 + model名正则(`[a-zA-Z0-9.:\-_/]`) + 长度限制
- /stats端点需管理token，无token→401

**版本号统一**
- 唯一真相源：`pyproject.toml` version = "1.6.1"
- formatters.py / telemetry/client.py / server/models.py / __init__.py
  全部改为 `importlib.metadata.version("kwcode")`，未安装时 fallback "1.6.1"

**测试：19个新测试**
- StrategyStats: record/get_best_sequence/min_attempts/persistence/corrupted recovery (5)
- UserPatternMemory: record/warning_threshold/top_errors/summary/unknown_ignored (6)
- SkillDrafter: draft_generation/save/exists/insufficient (4)
- TelemetryClient: disabled_default/enabled_config/non_blocking/skip_disabled (4)

### v1.5.0 — Isolated Search + Cross-File Contracts

Theory: WarpGrep (isolated search subagent) + CGM (graph-injected attention) + PENCIL (erase intermediate state) + SWE-ContextBench (context quality > model size)

**SearchSubagent** (`experts/search_subagent.py`)
- Independent context window — search noise never enters Generator
- Parallel file reads: ThreadPoolExecutor, 8 concurrent
- Returns only precise {file, start_line, end_line, content}
- Shadow TaskContext: Locator writes to shadow, main ctx stays clean

**UpstreamManifest** (`core/upstream_manifest.py`)
- Deterministic AST extraction: Python ast module, regex fallback for others
- Tracks: function signatures, constants, import dependencies
- get_constraints_for_file() → injected into Generator prompt
- check_consistency() → Verifier pre-check, catches arg count / constant mismatches
- Zero LLM calls

**PENCIL Compression + Contract Verification**
- task_compiler: _compact_subtask_result() keeps only signatures/constants/paths/test_status
- orchestrator: locator step uses SearchSubagent, verifier pre-checks contracts
- contract_violation error type triggers re-locate retry strategy
- Generator prompt receives upstream_constraints + retry_hint

**Code Quality (this release)**
- orchestrator.py run() split into 5 private methods (410→253 lines)
- Full type annotations: Optional[DebugSubagent], Callable[[str,str],None]
- __all__ added to 7 core modules
- pyproject.toml: license fixed, ruff + mypy configured
- TUI: 30+ event icons added (contract_violation, ab_test, replay, etc.)
- Server: /api/manifest endpoint, version 1.5.0

### v1.4.0 — Multi-Language + TUI + IDE

Theory: XRAY MCP Server + OpenCode + CodeCompass

- 7-language AST (Python/JS/TS/Go/Rust/Java/C#)
- ast-grep with QUERY_TEMPLATES (LLM never writes patterns)
- FastAPI server (port 7355) + SSE streaming
- Textual TUI (file tree + event log + input)
- VSCode extension (thin client, all logic server-side)

### v1.3.0 — EventBus + Error Strategy + Cognitive Gate

Theory: Dive into Claude Code + Wink + ARCS + SpecEyes + OPENDEV + Turn-Control

- EventBus: append-only log, replay, wildcard listeners
- ToolGateway: per-expert permissions, file cache with dirty tracking
- Error strategy routing: 6 error types → different retry sequences
- CognitiveGate: diminishing returns detection → auto-stop
- GraduatedCompactor: 3-layer progressive context compression
- Plan auto-trigger for hard tasks
- Worktree isolation (git worktree / tempdir fallback)
- Speculative Prefetch: background file pre-read
- SearchRouter: intent-aware routing (arXiv/S2/GitHub/PyPI/Open-Meteo)
- Wink self-repair: scope_creep / repetitive_fix / patch_miss / empty_output

### v1.2.0 — RIG Project Map

Theory: RIG + FastCode + CodeCompass

- export_rig(): full project index (exports/imports/routes/test coverage)
- upstream_summary structured dict for multi-task context passing
- ConsistencyChecker: deterministic frontend/backend API mismatch detection
- Gate/Locator prompt explicitly guided to query rig.json

### v1.1.0 — P0+P1+P2 Optimizations

- Verifier structured output (_classify_error: 5 error types)
- Circuit breakers (syntax 1x, import immediate, same-type 3x streak)
- Gate confidence scoring (0.92/0.75/0.55)
- Experience Replay (BM25 similar trajectory lookup)
- Session continuity (SessionState, 5-turn KWCODE.md re-injection)
- Locator minimal context (function boundaries, 60-line cap, gap markers)
- Watchdog 300s timeout
- Gate accuracy stats (/stats command)

### v0.9.0 — DAG Compiler + Debug Subagent

- TaskCompiler: DAG scheduler, ThreadPoolExecutor + Kahn topological sort
- Debug Subagent: sys.settrace variable capture on failure
- Prompt Optimizer: trajectory → experience rules → YAML system_prompt
- Cross-Encoder search reranking

### Core Pipeline (v0.5.0–v0.8.0)

- Gate → 6 pipeline routes (locator_repair/codegen/refactor/doc/office/chat)
- BM25+AST call graph two-phase location (zero LLM, milliseconds)
- Generator: original from file, LLM only generates modified
- Verifier: syntax check + pytest
- 3-stage retry + Reflection root cause analysis
- 5 deterministic tools (read_file/write_file/run_bash/list_dir/git)
- KWCODE.md project rules + /plan + Checkpoint + DocReader
- Model capability tiers (SMALL/MEDIUM/LARGE)
- Expert flywheel (trajectory → pattern → backtest → AB test → production)
- 3-layer memory (PROJECT.md/EXPERT.md/PATTERN.md)
- Office document generation (Excel/PPT/Word)
- MCP Router, context compression, CJK BM25

---

## Test Summary

| Category | Count | Status |
|----------|-------|--------|
| Core unit tests | 38 | PASS |
| Regression tests | 173 | PASS |
| P1 feature tests | 33 | PASS |
| P2 feature tests | 21 | PASS |
| Search refactor | 19 | PASS |
| Intent search | 19 | PASS |
| E2E real model | 17 | PASS |
| RIG modules | 29 | PASS |
| TaskCompiler | 12 | PASS |
| Multi-language | 51 | PASS |
| Server/TUI | 16 | PASS |
| SearchSubagent+Manifest | 27 | PASS |
| MoE Diagnostic | 62 | PASS |
| **Total** | **513** | **All green** |

---

## File Structure

```
kwcode/
├── pyproject.toml
├── README.md / README_zh.md
├── STATUS.md
└── kaiwu/
    ├── cli/
    │   ├── main.py              # 入口（173行）+ Typer路由
    │   ├── commands/task.py     # run/chat/vision/multi-task命令
    │   ├── commands/expert.py   # expert list/info/export/install
    │   ├── commands/config.py   # init/api/serve/setup-search
    │   ├── formatters.py        # Rich输出格式化
    │   ├── repl.py              # REPL交互循环
    │   ├── status_bar.py        # 状态栏(4档自适应)
    │   └── onboarding.py        # 首次启动引导
    ├── core/
    │   ├── event_bus.py         # Unified event bus (append-only + replay)
    │   ├── cognitive_gate.py    # Diminishing returns detection
    │   ├── wink.py              # Self-repair monitor
    │   ├── gate.py              # [v1.6] 确定性优先路由（Gap→关键词→LLM兜底）
    │   ├── orchestrator.py      # [v1.6] MoE pipeline + Gap驱动 + 回归检测
    │   ├── context.py           # TaskContext dataclass (+gap/confirmed_test_cmd/routing_source)
    │   ├── gap_detector.py      # [v1.6] GapType enum + GapDetector.compute() (zero LLM)
    │   ├── execution_state.py   # [v1.6] ExecutionStateTracker (regression detection)
    │   ├── env_prober.py        # [v1.6] EnvProber (toolchain/dep auto-fix, cached)
    │   ├── test_parser.py       # [v1.6] extract_failing/passing_tests (regex)
    │   ├── task_compiler.py     # DAG scheduler + WorktreeManager
    │   ├── upstream_manifest.py # [v1.5] Cross-file contract tracking (zero LLM)
    │   ├── planner.py           # /plan mode + risk assessment
    │   ├── checkpoint.py        # File snapshot (git stash / file copy)
    │   ├── kwcode_md.py         # KWCODE.md segmented loading
    │   ├── model_capability.py  # Model tier detection (SMALL/MEDIUM/LARGE)
    │   ├── context_pruner.py    # Context compression + GraduatedCompactor
    │   ├── network.py           # Network detection + proxy config
    │   └── sysinfo.py           # System info + VRAM monitoring
    ├── experts/
    │   ├── locator.py           # BM25+graph location + DocReader + Prefetch
    │   ├── search_subagent.py   # [v1.5] Isolated search (independent context)
    │   ├── generator.py         # [v1.6.1] Code generation + upstream_constraints system + retry last_code + small填空
    │   ├── verifier.py          # [v1.6] Syntax + pytest + whole_file + _detect_wrong_file
    │   ├── search_augmentor.py  # Search augmentation + BM25 rerank
    │   ├── consistency_checker.py # Frontend/backend API consistency (deterministic)
    │   ├── chat_expert.py       # Chat (search gating)
    │   └── office_handler.py    # Office document generation
    ├── search/                  # Intent-aware search routing
    ├── knowledge/               # PDF/Word/MD reader + CJK BM25
    ├── flywheel/                # Trajectory → pattern → generation → AB test
    ├── registry/                # Expert registry + .kwx packaging
    ├── notification/            # Flywheel notifications
    ├── stats/                   # Value tracking (SQLite)
    ├── memory/                  # 3-layer memory system
    ├── ast_engine/              # tree-sitter AST + call graph
    ├── server/                  # FastAPI + SSE (port 7355)
    ├── tui/                     # Textual TUI
    ├── mcp/                     # MCP Router
    ├── llm/                     # Ollama + llama.cpp backends
    ├── tools/                   # 5 deterministic tools + ToolGateway
    ├── audit/                   # [v1.6] Enhanced audit (success/failed split, iterations)
    └── tests/                   # 513 unit tests + 67 bench tasks + 62 diagnostic
        └── diagnostic/          # [v1.6] 4 architecture validation test suites
```

---

## TODO

1. ~~CLI拆分：main.py 1861→173行~~ ✅ v1.5.0
2. ~~注释统一中文~~ ✅ v1.5.0
3. ~~专家细粒度EventBus emit~~ ✅ v1.5.0
4. ~~bench tasks多语言覆盖（67题 Python/Go/TS）~~ ✅ v1.5.0
5. ~~删除WholeFileImplExpert/DependencyFixExpert，收敛到纯pipeline~~ ✅ v1.6.1
6. SQLite跨session查询
7. pip publish到PyPI（v1.5.0）
8. install.ps1 / install.sh一键安装
9. SWE-bench评测（用评测VPS跑）
10. **跑bench验证v1.6.1架构收敛效果**

## Known Issues

- qwen3-vl:8b outputs in thinking field, content empty (thinking extraction added)
- Reasoning models slow on Gate (8x multiplier)
- SearXNG requires Docker Desktop, degrades to DDG without it
