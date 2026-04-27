# Kaiwu v3 项目状态记录

> 项目路径：D:\program\codeagent2604\kaiwu
> 启动日期：2026-04-26
> 目标：本地模型 coding agent，通过确定性专家流水线让本地模型达到最高任务完成率

---

## 当前状态：v0.4 专家系统+飞轮已完成

MVP 流水线 + 6 步搜索增强 + 专家注册表 + 3 层记忆 + 飞轮自动生成 + 专家打包 + MCP Router 全部完成。

---

## v0.4 新增模块

### 专家注册表 (kaiwu/registry/)
- 12 个预置专家 YAML（api, bugfix, deepseekapi, docstring, fastapi, mybatis, refactor, springboot, sqlopt, testgen, typehint, uniapp）
- ExpertRegistry: 内存+磁盘双层，关键词饱和匹配（1命中=0.50, 2=0.75, 3=0.875）
- ExpertLoader: YAML 加载 + 校验
- ExpertPackager: .kwx 导入/导出（ZIP 格式）
- 生命周期状态机：new → mature → declining → archived

### 3 层记忆系统 (kaiwu/memory/)
- PROJECT.md — 项目级记忆（技术栈、架构、约定）
- EXPERT.md — 专家级记忆（每个专家的经验积累）
- PATTERN.md — 模式级记忆（跨项目的通用模式）

### 专家飞轮 (kaiwu/flywheel/)
- TrajectoryCollector — 任务执行轨迹记录（~/.kaiwu/trajectories/）
- PatternDetector — 重复成功模式检测（gate 1: >=5次同类型+同流水线+全成功）
- ExpertGeneratorFlywheel — LLM 从轨迹生成专家 YAML 草稿
- ABTester — 三门验证（gate 2 回测 + gate 3 AB 测试）
- LifecycleManager — 专家生命周期状态机

### 专家打包 (.kwx)
- `kaiwu expert export <name>` → 导出 .kwx 文件
- `kaiwu expert install <path.kwx>` → 安装到 ~/.kaiwu/experts/

### KaiwuMCP Router (kaiwu/mcp/)
- router_mcp.py — MCP 协议路由器
- `kaiwu serve-mcp` 启动 MCP 服务

### CLI 子命令
- `kaiwu expert list/info/export/install/remove/create`
- `kaiwu status` — 查看项目状态
- `kaiwu serve-mcp` — 启动 MCP 服务

---

## 验证结果

| 验证项 | 结果 | 备注 |
|--------|------|------|
| V1 Gate JSON稳定性 | 100% 解析成功率，67% 类型准确率 | gemma3:4b，不需要 grammar 约束 |
| V2 OpenHands集成 | 跳过，走 FLEX-1 自实现 | ToolExecutor 5个工具已完成 |
| V3 Locator精度 | 文件级 90%，函数级 20% | 函数级已加 few-shot 优化，待更大模型验证 |
| V4 搜索模块 | 意图4/4, DDG 4/4, Fetch 3/4, 压缩4/4 | trafilatura+bs4, 耗时略超15s(LLM瓶颈) |
| V5 AST Locator | A组(LLM)文件100%/函数50%, B组(AST)100%/100% | +50pp提升，值得集成 |
| V6 专家生成质量 | gemma3:4b 1/3, gemma4:e2b 3/3 PASS | 小模型JSON生成弱，大模型全过 |
| E2E 单文件 | 通过 | gemma3:4b 5.7s / gemma4:e2b 64.9s，5/5 测试 |
| E2E 多文件 | 通过 | gemma3:4b 7.7s，password leak 跨2文件，3/3 测试 |
| gemma4:e2b Gate | 100% 类型准确率（含 office） | 比 gemma3:4b 的 67% 大幅提升，但慢 10x |

---

## 搜索模块架构（2026-04-26 追加）

```
SearchAugmentorExpert.search(ctx)
  ① IntentClassifier    — 纯关键词，毫秒级，github/arxiv/pypi/bug/general
  ② QueryGenerator      — 一次LLM调用，2-3条英文query
  ③ DuckDuckGoSearcher  — bs4解析HTML，零API key
  ④ QualityFilter       — 域名黑白名单，最多3个URL
  ⑤ ContentFetcher      — trafilatura(降级httpx)，每页≤800字
  ⑥ ContextCompressor   — 一次LLM调用，≤400字摘要
```

---

## 架构

```
用户输入（CLI）
    │
    ▼
  ExpertRegistry.match()  ← 关键词匹配，毫秒级
    │ 命中 → 注入 expert.system_prompt
    │ 未命中 → 走 Gate
    ▼
  Gate（单次LLM调用，结构化JSON路由）
    │
    ▼ 按 expert_type 选择流水线
  ┌─────────────────────────────────┐
  │ locator_repair: Locator→Generator→Verifier │
  │ codegen:        Generator→Verifier          │
  │ refactor:       Locator→Generator→Verifier  │
  │ doc:            Generator                    │
  │ office:         OfficeHandler (stub)         │
  └─────────────────────────────────┘
    │
    ▼ 失败重试（最多3次，2次失败触发搜索增强）
  SearchAugmentor → 重新跑流水线
    │
    ▼
  TrajectoryCollector 记录轨迹
    │
    ▼
  3层记忆写入 (PROJECT.md / EXPERT.md / PATTERN.md)
    │
    ▼ 后台飞轮
  PatternDetector → ExpertGenerator → ABTester → LifecycleManager
```

## 文件结构

```
kaiwu/
├── pyproject.toml
└── kaiwu/
    ├── cli/main.py              # CLI入口 typer+rich (expert/status/serve-mcp子命令)
    ├── core/
    │   ├── context.py           # TaskContext 数据类
    │   ├── gate.py              # Gate 分类器
    │   └── orchestrator.py      # 流水线编排器
    ├── experts/
    │   ├── locator.py           # 文件→函数 两阶段定位 (符号索引辅助)
    │   ├── generator.py         # 从文件读original，LLM只生成modified
    │   ├── verifier.py          # 语法检查 + pytest 验证
    │   ├── search_augmentor.py  # 6步搜索流水线编排
    │   └── office_handler.py    # MVP stub
    ├── registry/
    │   ├── expert_registry.py   # 内存+磁盘双层注册表，关键词饱和匹配
    │   ├── expert_loader.py     # YAML加载+校验
    │   └── expert_packager.py   # .kwx导入/导出 (ZIP格式)
    ├── builtin_experts/         # 12个预置专家YAML
    │   ├── api.yaml
    │   ├── bugfix.yaml
    │   ├── fastapi.yaml
    │   ├── testgen.yaml
    │   └── ... (12个)
    ├── flywheel/
    │   ├── trajectory_collector.py  # 轨迹记录 → ~/.kaiwu/trajectories/
    │   ├── pattern_detector.py      # gate 1: 重复模式检测
    │   ├── expert_generator.py      # LLM生成专家YAML草稿
    │   ├── ab_tester.py             # gate 2+3: 回测+AB测试
    │   └── lifecycle_manager.py     # 专家生命周期状态机
    ├── memory/
    │   ├── project_md.py        # PROJECT.md 项目级记忆
    │   ├── expert_md.py         # EXPERT.md 专家级记忆
    │   ├── pattern_md.py        # PATTERN.md 模式级记忆
    │   └── kaiwu_md.py          # KAIWU.md 兼容旧版
    ├── mcp/
    │   └── router_mcp.py        # KaiwuMCP Router
    ├── search/
    │   ├── intent_classifier.py # 纯关键词意图分类
    │   ├── query_generator.py   # LLM生成英文query
    │   ├── duckduckgo.py        # DDG HTML scraper (bs4)
    │   ├── quality_filter.py    # 域名黑白名单
    │   ├── content_fetcher.py   # trafilatura/httpx正文提取
    │   └── context_compressor.py# LLM压缩摘要
    ├── llm/llama_backend.py     # llama.cpp + Ollama 双后端
    ├── tools/
    │   ├── executor.py          # read/write/bash/list/git 工具层
    │   └── ast_utils.py         # AST符号提取
    ├── tests/test_core.py       # 24个单元测试
    └── validation/              # V1-V6 验证脚本 + 结论JSON
        ├── v1_gate_stability.py
        ├── v2_openhands_check.py
        ├── v3_locator_accuracy.py
        ├── v4_search_module.py
        ├── v5_ast_locator.py
        └── v6_expert_generation.py
```

---

## 踩坑记录（经验教训）

### 1. Reasoning 模型的 stop 参数会截断 thinking

**现象**：deepseek-r1:8b 通过 Ollama 调用时，content 始终为空。
**根因**：Gate 传了 `stop=["\n\n"]`，reasoning 模型的 `<think>` 块内有空行，stop 在 thinking 阶段就触发了截断，content 还没生成就结束了。
**修复**：对 reasoning 模型不传 stop 参数。
**教训**：reasoning 模型的 thinking tokens 是"隐形"的，所有影响生成终止的参数（stop、max_tokens）都要考虑 thinking 的开销。

### 2. Ollama 对 temperature=0 的请求有 KV cache

**现象**：修复代码后重跑测试，deepseek-r1 仍然返回空。
**根因**：之前 temperature=0 的空结果被 Ollama 缓存了，后续相同 prompt 直接返回缓存。
**修复**：reasoning 模型 temperature=0 改为 0.01；测试前 `POST /api/generate {"model": "xxx", "keep_alive": 0}` 卸载模型清缓存。
**教训**：Ollama 的缓存机制对调试有干扰，遇到"代码改了但结果不变"时先怀疑缓存。

### 3. Generator 的 original 不能让 LLM 生成

**现象**：Generator 让 LLM 同时输出 original 和 modified，但 LLM 输出的 original 经常省略注释行或空行，导致 apply_patch 精确匹配失败。
**根因**：小模型复述代码时会"改写"而不是精确复制。
**修复**：original 从文件直接读取（`_extract_function` 按缩进提取完整函数），LLM 只生成 modified。
**教训**：凡是需要精确匹配的内容，绝对不要让 LLM 生成。LLM 负责创造，代码负责精确。

### 4. Verifier 的 pytest 命令要指定 tests/ 目录

**现象**：patch apply 成功，但 Verifier 报 `ModuleNotFoundError`。
**根因**：`pytest --tb=short -q` 没指定目录，pytest 从 cwd 递归收集，可能收集到上层目录的测试文件导致 import 冲突。
**修复**：改为 `python -m pytest tests/ --tb=short -q`。
**教训**：subprocess 跑测试时，路径隔离很重要。

### 5. 不要在 apply_patch 里做 fuzzy match

**尝试**：为了兼容 LLM 输出的不精确 original，在 apply_patch 里加了行级 fuzzy match 和 LLM merge fallback。
**结果**：增加了复杂度但没解决根因，fuzzy match 的边界条件很多。
**正确做法**：从源头解决——original 从文件读取，保证 100% 精确匹配。apply_patch 只做 exact match。
**教训**：下游打补丁不如上游修根因。

### 6. deepseek-r1:8b 的 /api/generate 完全不可用

**现象**：`/api/generate` 返回空 response，`done_reason: length`。
**根因**：thinking tokens 消耗了全部 `num_predict` 配额，content 没有预算。`/api/chat` 会把 thinking 和 content 分开计算。
**修复**：Ollama 后端统一走 `/api/chat`，不用 `/api/generate`。
**教训**：reasoning 模型必须用 chat API。

### 7. gemma3:4b 的 office 类分类准确率低

**现象**：V1 验证中 office 类 20 条只有 4 条正确，大部分被分为 codegen。
**根因**：4B 模型对"Excel/Word/PPT"这类关键词的语义理解不够，倾向于把"生成"类任务都归为 codegen。
**影响**：不影响 MVP（office 是 stub），但换更大模型后需要重新验证。
**教训**：Gate 的分类准确率直接依赖模型能力，小模型适合粗粒度分类（3-4 类），细粒度需要更大模型。

### 8. trafilatura.fetch_url 没有超时控制

**现象**：V4 验证每个 case 耗时 30-120s，远超 15s 红线。
**根因**：`trafilatura.fetch_url(url)` 内部用 urllib，默认无超时，遇到慢站点会阻塞很久。
**修复**：不用 `trafilatura.fetch_url`，改为 `httpx.get(url, timeout=5.0)` 自己下载 HTML，再传给 `trafilatura.extract()` 做正文提取。
**教训**：第三方库的网络请求一定要自己控制超时，不要信任库的默认值。

### 9. StackOverflow 403 拒绝爬虫

**现象**：V4 验证 bug 类 case fetch 全部失败。
**根因**：StackOverflow 对非浏览器 User-Agent 返回 403。
**影响**：MVP 可接受（snippet 兜底），后续可加 cloudscraper 或更真实的 UA。
**教训**：高质量源不一定能爬到，QualityFilter 的白名单排序不等于能 fetch 成功。

---

## 下一步计划

- [x] git init + 首次提交
- [x] 搜索模块 6 步流水线
- [x] CLI 交互式 REPL（/model /cd /plan /help 等命令）
- [x] 函数级定位优化（AST 提取候选 → LLM 选择，单函数文件跳过 LLM）
- [x] StackOverflow 403 修复（StackExchange API）
- [x] 符号索引辅助文件定位（跨文件 bug 修复验证通过）
- [x] 多文件修改 E2E（password leak 跨 models.py+service.py，3/3 测试通过）
- [x] codegen 流水线验证（纯生成通过，但写到 new_code.py 而非目标文件）
- [x] 拉更大模型验证（gemma4:e2b Gate 100%准确率，E2E通过）
- [x] Windows 兼容性（GBK编码修复）
- [x] Gate codegen/locator_repair 边界优化（prompt 明确描述，5/5 边界 case 通过）
- [x] 性能优化：reasoning模型think=false，gemma4 64.9s→19.3s（3.4x提速）
- [x] 非Python语言支持（JS/Go/Rust regex提取验证通过）
- [x] 专家注册表（12个预置专家，关键词匹配，生命周期状态机）
- [x] 3层记忆系统（PROJECT.md / EXPERT.md / PATTERN.md）
- [x] 专家飞轮（轨迹收集 → 模式检测 → 专家生成 → 三门验证 → 生命周期）
- [x] 专家打包（.kwx 导入/导出）
- [x] KaiwuMCP Router
- [x] CLI 子命令（expert list/info/export/install/remove/create, status, serve-mcp）
- [x] V5/V6 验证脚本框架（就绪，需要Ollama在线运行）
- [x] 安装脚本（install.ps1 + install.sh，国内镜像适配）
- [x] 中文文档（README_zh.md）
- [x] E2E 端到端验收（fibonacci off-by-one，gemma3:4b，22.4s，4/4测试，含重试+搜索+记忆+轨迹）
- [x] Windows cmd原生验证（Python import + pytest 24/24 通过）
- [x] 红线约束代码review（10/10 CORE 全部 PASS）
- [x] V5 AST Locator验证（A组函数50% vs B组100%，+50pp，AST值得集成）
- [x] V6 专家生成质量验证（gemma4:e2b 3/3 PASS，gemma3:4b 1/3）
- [x] 预置专家抽样验证（BugFix 5/5=100%, TestGen gemma4 3/5=60%）
- [x] CLI补全（--no-search, memory --reset）
- [x] 中国网络优化（DDG→Bing fallback, httpx代理, ModelScope自动切换, 安装脚本网络探测）
- [x] CLI命令改名 kaiwu → kwqode（包名不变，入口+显示名+MCP工具名全部更新）
- [x] 飞轮端到端验证（5次任务→模式检测→专家生成→Gate2通过→注册→lifecycle new→mature→declining）

### 已知限制

- TestGenExpert 受限于小模型生成测试代码质量，gemma4:e2b 60%
- V3 验证脚本的临时目录路径匹配有问题，不影响真实场景
- 跨设备迁移(backup/restore)和SQLite跨session查询为后续优化项
