# KWCode 项目状态记录

> 项目路径：D:\program\codeagent2604\kwcode
> GitHub：https://github.com/val1813/kwcode
> 启动日期：2026-04-26
> 目标：本地模型 coding agent，通过确定性专家流水线让本地模型达到最高任务完成率

---

## 当前状态：v1.3.0 (2026-05-06)

357/357 测试全绿。v2 架构升级完成：EventBus + ToolGateway + 错误策略路由 + 认知门控 + 渐进压缩 + Plan自动触发 + Worktree隔离 + Speculative Prefetch + SearchRouter + Wink自修复 + 搜索层网络保护。

### v1.3.0 新增：v2 架构升级（10 个模块）

理论来源：Dive into Claude Code(arXiv:2604.14228) + Wink(arXiv:2602.17037) + ARCS(arXiv:2504.20434) + SpecEyes(arXiv:2603.23483) + OPENDEV(arXiv:2603.05344) + Turn-Control(arXiv:2510.16786)

**模块1: EventBus 统一事件总线** (`core/event_bus.py`)
- append-only 日志支持 replay/时间旅行调试
- on/off/emit 三个核心方法，wildcard "*" 监听所有事件
- CLI 接入追加式渲染（EVENT_ICONS 17种事件图标）

**模块2: ToolGateway 工具权限层** (`tools/tool_gateway.py`)
- 专家权限白名单（generator只读不写，verifier可写可执行）
- 文件读缓存 + 脏标记（写后自动失效缓存）
- 所有工具调用通过 EventBus 可观测

**模块3: 错误策略路由** (`core/orchestrator.py` RETRY_STRATEGIES)
- 按 error_type 切换重试序列（syntax/assertion/import/patch_apply/runtime/unknown）
- import 错误：确定性修复器 `tools/import_fixer.py`（不调LLM）
- _build_retry_hint() 按错误类型生成精准重试提示
- _should_search() 按失败类型决定是否搜网络（不再统一 retry>=2 时搜）

**模块4: 认知门控 CognitiveGate** (`core/cognitive_gate.py`)
- patch 行数持续递减 → 边际收益递减 → 自动停止
- 连续输出相同行数 → 原地打转 → 自动停止
- 最后一次极小(≤3行) → 模型无从下手 → 自动停止

**模块5: 上下文渐进压缩 GraduatedCompactor** (`core/context_pruner.py`)
- Layer 1 (70%): 裁剪 tool 输出冗余（>500 token 提取关键词）
- Layer 2 (85%): 复用 ContextPruner 压缩中间轮次
- Layer 3 (95%): 摘要化早期对话，只保留关键决策

**模块6: Plan 自动触发** (`core/orchestrator.py`)
- hard 任务自动生成执行计划（不打断用户）
- 低中风险直接执行，高风险暂停确认

**模块7: Worktree 隔离** (`core/task_compiler.py` WorktreeManager)
- Git 项目：git worktree 隔离
- 非 Git 项目：tempdir + copytree
- cleanup() 支持 merge 回主分支

**模块8: Speculative Prefetch** (`experts/locator.py`)
- Locator 完成后后台线程预读文件到内存
- 减少 Generator 阶段 IO 等待

**模块9: SearchRouter 意图感知搜索** (`search/search_router.py`)
- 零 key 默认可用：arXiv / Semantic Scholar / GitHub REST / PyPI JSON / Open-Meteo
- 按意图路由：research/code_solution/code_example/weather/library_doc/general
- 可选 Tavily key 提升通用搜索质量
- 错误驱动搜索接入 orchestrator

**模块10: Wink 自修复监控** (`core/wink.py`)
- scope_creep: easy任务定位>5文件 → 纠正
- repetitive_fix: 同类错误≥2次 → 换思路
- patch_miss: patch_apply失败 → 重新读文件
- empty_output: Generator无输出 → 简化任务

**搜索层网络保护补丁**
- duckduckgo.py: DDG为主，SearXNG为可选增强，不自动拉起Docker
- search_augmentor.py: 全局 try/except 保护，任何网络问题返回空
- orchestrator.py: 搜索结果为空不阻塞，异常不中断重试流程
- config.yaml: search_enabled 开关（环境变量 KWCODE_SEARCH_ENABLED）
- 内网/离线用户设置 false 永远不触发网络请求

### v1.2.0 新增：RIG侦察层（Project Map）

理论来源：RIG(arXiv:2601.10112) + FastCode(arXiv:2603.01012) + CodeCompass工具采用率研究

**RIG-1: export_rig() 仓库结构索引** (`ast_engine/graph_builder.py`)
- 扫描全项目Python文件提取exports/imports（regex，零LLM）
- 检测Flask/FastAPI路由装饰器 → api_routes
- 匹配test_foo.py → foo.py → test_coverage
- 扫描.js/.ts文件检测axios/fetch调用 → frontend_api_calls
- 双文件输出：.kaiwu/rig.json（完整索引）+ .kaiwu/rig_summary.json（精简骨架<5KB）
- 精简骨架动态截断文件列表，保证注入Gate/Locator不爆context

**RIG-2: upstream_summary结构化** (`core/context.py` + `core/task_compiler.py`)
- upstream_summary从str改为dict: {modified_files, diffs, new_symbols, broken_interfaces}
- TaskCompiler自动提取上游patch的文件/diff/新符号，结构化传递给下游子任务
- 新增_format_upstream_text()将结构化dict转为LLM可读文本注入prompt
- 下游子任务Locator可直接读取"哪些文件被改了"，不靠模型猜

**RIG-3: ConsistencyChecker前后端一致性检查** (`experts/consistency_checker.py`)
- 基于rig.json做确定性集合对比（不调LLM）
- 输出backend_only/frontend_only/matched不一致清单
- check_with_details()带文件位置信息，可直接作为子任务输入
- format_for_subtask()生成可注入Generator的文本

**RIG-4: Gate/Locator prompt显式引导查rig** (`core/gate.py` + `experts/locator.py`)
- Gate prompt新增显式引导："优先参考.kaiwu/rig.json理解项目结构"
- Locator prompt新增{rig_context}占位符
- _load_rig_context()读取rig_summary.json（不是完整rig.json），注入路由/前端调用/测试覆盖摘要
- 解决CodeCompass发现的工具采用率42%问题：模型不查图的根因是prompt没引导

### v1.1.0 新增：P0+P1+P2 全量优化

**P2-1: Watchdog任务超时** (`core/orchestrator.py`)
- threading.Timer 300秒硬超时，卡死任务自动终止
- 重试循环每轮检查watchdog状态

**P2-2: Gate路由准确率统计** (`stats/value_tracker.py` + `cli/main.py`)
- `get_gate_accuracy()` 按expert_type统计成功率/平均耗时/平均重试
- 新增 `/stats` CLI命令展示30天统计报告

**其他**
- 新增 CONTRIBUTING.md（架构红线、PR标准、贡献类型指南）
- README 贡献章节重写（精简+链接CONTRIBUTING.md）
- 删除 vision_expert.py.bak（vision_expert保留为辅助输入通道）

---

#### P0+P1 优化（7文件 +362行）

**P0-1: Verifier结构化输出** (`experts/verifier.py`)
- `_classify_error()` 纯正则提取 error_type/error_file/error_line/error_message/failed_tests
- 5种错误类型：syntax/assertion/import/runtime/patch_apply
- 所有错误路径统一返回结构化字段，DebugSubagent不再需要自己解析

**P0-2: 熔断器+缩小scope** (`core/orchestrator.py`)
- syntax错误1次后直接熔断（重试无意义）
- import错误立即熔断+提示安装依赖
- 同类error_type连续3次→硬熔断
- 第2次失败自动缩小scope到第一个文件+函数
- 低置信度(<0.6)任务自动减少重试预算

**P0-3: Gate置信度输出** (`core/gate.py`)
- `_estimate_confidence()` 关键词信号强度评分（0.92/0.75/0.55三档）
- 不覆盖expert_registry已有的confidence
- orchestrator消费：低置信度减少max_retries

**P1-1: Experience Replay** (`flywheel/trajectory_collector.py` + `core/orchestrator.py` + `core/context.py`)
- `find_similar()` BM25检索历史成功轨迹（复用已有rank-bm25依赖）
- orchestrator.run()开头自动调用，结果存入ctx.similar_trajectories
- 飞轮闭环：同类任务不走冷启动

**P1-2: Session内多轮连贯** (`cli/main.py`)
- SessionState类：跟踪tasks/files_touched/turn_count
- `to_reminder()` 生成System Reminder注入Gate memory_context
- 每5轮重新注入KWCODE.md核心规则（注意力衰减对抗）

**P1-3: Locator最小上下文裁剪** (`experts/locator.py`)
- 函数边界识别（indent-based，def到下一个同级def）
- 去掉纯注释行，docstring限3行
- 60行/函数上限，gap marker标记不连续区域
- 文件路径header + 行号前缀

### v0.9.0 新增

**DAG 任务编译器（TaskCompiler）**
- `core/task_compiler.py`：轻量 DAG 调度器，ThreadPoolExecutor + Kahn 拓扑排序
- 支持串行（依赖链）和并行（独立任务）混合执行
- 依赖上下文注入：前置任务结果自动追加到后续任务输入
- 零新依赖，12 个测试覆盖（串行/并行/菱形DAG/环检测）

**Debug Subagent（运行时调试子代理）**
- `experts/debug_subagent.py`：基于 Debug2Fix 论文（Microsoft 2026）
- verifier 失败后用 sys.settrace 非侵入式捕获目标行变量值
- LLM 决定调试策略（断点位置+变量列表），fallback 到 pytest --tb=long
- 调试结果注入 generator retry prompt，让重试拿到真实运行时数据
- 15 个测试覆盖

**Prompt Optimizer（飞轮优化 YAML system_prompt）**
- `flywheel/prompt_optimizer.py`：分析成功轨迹 → Opus/Sonnet API 生成经验规则
- 规则追加到专家 YAML 的 system_prompt（`## 经验规则（自动生成）`）
- 替代已删除的 Python 代码优化器，方向正确：优化知识而非代码

**Reflexion 持久化**（保留）
- REFLECTION.md 结构化写入 + /plan 风险提示注入

**Cross-Encoder 搜索重排**（保留）
- BM25 后追加 Cross-Encoder 精排，FLEX-2 自动降级

### v0.8.0 已移除（方向错误）

- ~~ExpertBase 继承体系~~（Python专家把知识和执行逻辑混在一起）
- ~~BugFixExpert.py~~（应该是 YAML 知识载体，不是 Python 类）
- ~~SelfImprovingOptimizer~~（优化 Python 代码 → 改为优化 YAML prompt）
- ~~Registry Python 专家加载~~（回退到纯 YAML）

### 已完成功能清单

**MVP 核心流水线**
- Gate LLM分类 → 6种流水线路由（locator_repair/codegen/refactor/doc/office/chat）
- BM25+AST调用图两阶段定位（零LLM，毫秒级）
- Generator 从文件读original，LLM只生成modified
- Verifier 语法检查 + pytest 自动验证
- 三阶段重试（正常→从错误出发→最小化修改）+ Reflection根因分析
- 5个确定性工具（read_file/write_file/run_bash/list_dir/git）

**P1 四大功能（v0.5.0）**
- KWCODE.md 项目规则文件（分段加载+按任务类型注入+token上限15%）
- /plan 计划模式 + 三档风险评估（High/Medium/Low，基于历史失败记录）
- Checkpoint 文件快照（git stash主路径+文件复制兜底+失败自动还原+降级建议）
- 非代码文件读取（PDF/Word/MD/TXT + BM25Plus段落匹配 + Locator自动注入）

**P2 三大功能（v0.6.0）**
- 模型能力自适应（SMALL/MEDIUM/LARGE三档策略，自动检测，小模型强制plan）
- 飞轮可见性通知（专家投产Panel+积累进度+里程碑，不打断任务）
- 价值量化仪表盘（SQLite本地统计+kwcode stats命令+启动周报）

**搜索模块重构（v0.6.1）**
- 四级内容提取管道（trafilatura→newspaper→readabilipy→soup，质量评分选最佳）
- SearXNG + DDG 并行搜索（ThreadPoolExecutor + URL去重合并）
- kwcode setup-search 一键安装SearXNG

**意图感知搜索（v0.6.2）**
- 意图分类器增强（5类意图+关键词扩充+LLM fallback语义分类）
- ChatExpert搜索门控（follow-up/推理不搜索，实时数据始终搜索）
- QueryGenerator按意图生成更精准搜索词
- BM25Plus搜索结果重排

**UI全面优化（v0.7.0）**
- 删掉所有机器内部信息（logger只写~/.kwcode/kwcode.log，warnings静默）
- 执行过程spinner动画（rich.progress，transient=True完成后消失）
- 完成后用户友好结果摘要（修改文件+改动bullet+测试结果）
- 重影大字KAIWU Header + 状态栏深色背景
- kwcode setup-search 一键安装SearXNG搜索引擎

**其他**
- 专家注册表（15个预置专家YAML + .kwx导入导出）
- 专家飞轮三道门（轨迹→模式检测→回测→AB测试→投产）
- 3层记忆系统（PROJECT.md/EXPERT.md/PATTERN.md）
- Office文档生成（Excel/PPT/Word）
- MCP Router（kwcode serve-mcp）
- 上下文压缩（纯算法，头尾保留+中间关键词提取，<10ms）
- 中文分词BM25（DocReader CJK tokenizer）
- /api 命令（临时/永久切换任意OpenAI兼容API）

### E2E 验收结果（2026-04-28，gemma3:4b）

- P1: KWCODE.md注入✓ /plan风险评估✓ Checkpoint还原✓ DocReader注入✓ (8/8)
- P2: 模型自适应(4b→SMALL)✓ 飞轮通知✓ ValueTracker✓ (6/6)
- 集成: Gate分类✓ Chat流水线✓ Codegen流水线(2.9s)✓ (3/3)

### 测试统计

| 类别 | 数量 | 状态 |
|------|------|------|
| 核心单元测试 | 38 | PASS |
| 回归测试 | 173 | PASS |
| P1 功能测试 | 33 | PASS |
| P2 功能测试 | 21 | PASS |
| 搜索重构测试 | 19 | PASS |
| 意图搜索测试 | 19 | PASS |
| E2E 真实模型 | 17 | PASS |
| RIG模块测试 | 29 | PASS |
| TaskCompiler测试 | 12 | PASS |
| **合计** | **357** | **全绿** |

### 待做

1. SQLite 跨 session 查询（spec §7.1 kaiwu.db）
2. 12 个预置专家完整 benchmark（目前只跑了 BugFix+TestGen）
3. 实时数据API提示注入（codegen涉及天气/股价时，prompt注入免费API信息，避免模型编造假数据）
4. 多语言AST支持（JavaScript/TypeScript/Java/Go/Rust 调用图）
5. pip publish 到 PyPI
6. install.ps1 / install.sh 一键安装脚本

### 已知问题

- qwen3-vl:8b 所有输出在thinking字段，content为空（已加thinking提取）
- reasoning模型Gate调用慢（8x multiplier）
- SearXNG需要Docker Desktop，无Docker时降级到DDG
- codegen生成网页时模型可能用Math.random()假数据（需实时数据API提示注入）

---

## 文件结构

```
kwcode/
├── pyproject.toml
├── README.md / README_zh.md
├── STATUS.md
└── kaiwu/
    ├── cli/
    │   ├── main.py              # REPL + EventBus追加式渲染 + spinner + 结果摘要
    │   ├── status_bar.py        # 状态栏(4档自适应) + TokPerSecEstimator
    │   └── onboarding.py        # 首次启动引导
    ├── core/
    │   ├── event_bus.py         # [v1.3] 统一事件总线(append-only日志+replay)
    │   ├── cognitive_gate.py    # [v1.3] 认知门控(patch行数递减检测)
    │   ├── wink.py              # [v1.3] Wink自修复监控(偏离检测+纠正注入)
    │   ├── gate.py              # LLM任务分类 → 专家知识叠加
    │   ├── orchestrator.py      # 确定性流水线 + 错误策略路由 + Plan自动触发
    │   ├── context.py           # TaskContext数据类
    │   ├── task_compiler.py     # DAG调度器 + WorktreeManager隔离
    │   ├── planner.py           # /plan计划模式 + 风险评估
    │   ├── checkpoint.py        # 文件快照(git stash/文件复制)
    │   ├── kwcode_md.py         # KWCODE.md分段加载+注入
    │   ├── model_capability.py  # 模型三档自适应(SMALL/MEDIUM/LARGE)
    │   ├── context_pruner.py    # 上下文压缩 + GraduatedCompactor 3层渐进压缩
    │   ├── network.py           # 网络探测+代理配置
    │   └── sysinfo.py           # 系统信息+VRAM监控
    ├── experts/
    │   ├── locator.py           # BM25+调用图定位 + DocReader注入 + Speculative Prefetch
    │   ├── generator.py         # 代码生成(original从文件读，LLM只写modified)
    │   ├── verifier.py          # 语法检查 + pytest
    │   ├── search_augmentor.py  # 搜索增强 + BM25重排 + 网络保护
    │   ├── consistency_checker.py # 前后端接口一致性检查(确定性，不调LLM)
    │   ├── chat_expert.py       # 聊天(搜索门控：follow-up/推理不搜)
    │   └── office_handler.py    # Office文档生成
    ├── search/
    │   ├── search_router.py     # [v1.3] 意图感知搜索路由(arxiv/S2/GitHub/PyPI/Open-Meteo)
    │   ├── duckduckgo.py        # DDG主+SearXNG可选 + search_enabled开关
    │   ├── extraction_pipeline.py  # 四级内容提取
    │   ├── intent_classifier.py # 意图感知(5类+LLM fallback)
    │   ├── query_generator.py   # 按意图生成搜索词
    │   ├── content_fetcher.py   # 薄封装→extraction_pipeline
    │   └── quality_filter.py    # 域名黑白名单
    ├── knowledge/doc_reader.py  # PDF/Word/MD读取 + CJK分词BM25
    ├── flywheel/                # 轨迹→模式→生成→AB测试→投产
    ├── registry/                # 专家注册表 + .kwx打包
    ├── notification/            # 飞轮通知(expert_born/progress/milestone)
    ├── stats/                   # 价值量化(SQLite)
    ├── memory/                  # 三层记忆(PROJECT/EXPERT/PATTERN)
    ├── ast_engine/              # tree-sitter AST + 调用图(SQLite)
    ├── mcp/                     # MCP Router
    ├── llm/                     # Ollama + llama.cpp双后端
    ├── tools/                   # 5个确定性工具 + ToolGateway + import_fixer
    └── tests/                   # 357个测试
```
