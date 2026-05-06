# KWCode Project Status

> Path: D:\program\codeagent2604\kwcode
> GitHub: https://github.com/val1813/kwcode
> Started: 2026-04-26
> Goal: Local-model coding agent — maximize task completion rate via deterministic expert pipeline

---

## Current: v1.5.1 (2026-05-06)

470/470 tests green (451旧 + 19新) + 67个bench tasks。
三飞轮系统 + 匿名遥测(opt-in) + 服务端部署(https://llmbbs.com)。

### v1.5.1 — Flywheel + Anonymous Telemetry

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
| **Total** | **451** | **All green** |

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
    │   ├── gate.py              # LLM task classification → expert routing
    │   ├── orchestrator.py      # Deterministic pipeline + error strategy routing
    │   ├── context.py           # TaskContext dataclass
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
    │   ├── generator.py         # Code generation (original from file, LLM writes modified)
    │   ├── verifier.py          # Syntax + pytest + cross-file contract check
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
    └── tests/                   # 451 unit tests + 67 bench tasks (Python/Go/TS)
```

---

## TODO

1. ~~CLI拆分：main.py 1861→173行~~ ✅ v1.5.0
2. ~~注释统一中文~~ ✅ v1.5.0
3. ~~专家细粒度EventBus emit~~ ✅ v1.5.0
4. ~~bench tasks多语言覆盖（67题 Python/Go/TS）~~ ✅ v1.5.0
5. SQLite跨session查询
6. pip publish到PyPI（v1.5.0）
7. install.ps1 / install.sh一键安装
8. SWE-bench评测（用评测VPS跑）

## Known Issues

- qwen3-vl:8b outputs in thinking field, content empty (thinking extraction added)
- Reasoning models slow on Gate (8x multiplier)
- SearXNG requires Docker Desktop, degrades to DDG without it
