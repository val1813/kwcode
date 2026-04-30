# KWCode 项目状态记录

> 项目路径：D:\program\codeagent2604\kwcode
> GitHub：https://github.com/val1813/kwcode
> 启动日期：2026-04-26
> 目标：本地模型 coding agent，通过确定性专家流水线让本地模型达到最高任务完成率

---

## 当前状态：v0.9.0 (2026-04-29)

292/292 测试全绿。Python专家系统已移除，改为正确方向。

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
| **合计** | **282** | **全绿** |

### 待做

1. SQLite 跨 session 查询（spec §7.1 kaiwu.db）
2. 12 个预置专家完整 benchmark（目前只跑了 BugFix+TestGen）
3. 实时数据API提示注入（codegen涉及天气/股价时，prompt注入免费API信息，避免模型编造假数据）
4. 多语言AST支持（Go MVP 已完成；JavaScript/TypeScript/Java/Rust 调用图待做）
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
    │   ├── main.py              # REPL + spinner + 结果摘要 + 重影Header + setup-search
    │   ├── status_bar.py        # 状态栏(4档自适应) + TokPerSecEstimator
    │   └── onboarding.py        # 首次启动引导
    ├── core/
    │   ├── gate.py              # LLM任务分类 → 专家知识叠加
    │   ├── orchestrator.py      # 确定性流水线 + KWCODE.md注入 + Checkpoint + ValueTracker
    │   ├── context.py           # TaskContext数据类
    │   ├── planner.py           # /plan计划模式 + 风险评估
    │   ├── checkpoint.py        # 文件快照(git stash/文件复制)
    │   ├── kwcode_md.py         # KWCODE.md分段加载+注入
    │   ├── model_capability.py  # 模型三档自适应(SMALL/MEDIUM/LARGE)
    │   ├── context_pruner.py    # 上下文压缩(纯算法，<10ms)
    │   ├── network.py           # 网络探测+代理配置
    │   └── sysinfo.py           # 系统信息+VRAM监控
    ├── experts/
    │   ├── locator.py           # BM25+调用图定位 + DocReader注入
    │   ├── generator.py         # 代码生成(original从文件读，LLM只写modified)
    │   ├── verifier.py          # 语法检查 + pytest
    │   ├── search_augmentor.py  # 搜索增强 + BM25重排
    │   ├── chat_expert.py       # 聊天(搜索门控：follow-up/推理不搜)
    │   └── office_handler.py    # Office文档生成
    ├── search/
    │   ├── duckduckgo.py        # SearXNG+DDG并行搜索
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
    ├── tools/                   # 5个确定性工具
    └── tests/                   # 282个测试
```
