# Kaiwu v3 项目状态记录

> 项目路径：D:\program\codeagent2604\kaiwu
> 启动日期：2026-04-26
> 目标：本地模型 coding agent，通过确定性专家流水线让本地模型达到最高任务完成率

---

## 当前状态：搜索模块已完成

MVP 流水线 + 6 步搜索增强全部跑通。

---

## 验证结果

| 验证项 | 结果 | 备注 |
|--------|------|------|
| V1 Gate JSON稳定性 | 100% 解析成功率，67% 类型准确率 | gemma3:4b，不需要 grammar 约束 |
| V2 OpenHands集成 | 跳过，走 FLEX-1 自实现 | ToolExecutor 5个工具已完成 |
| V3 Locator精度 | 文件级 90%，函数级 20% | 函数级已加 few-shot 优化，待更大模型验证 |
| V4 搜索模块 | 意图4/4, DDG 4/4, Fetch 3/4, 压缩4/4 | trafilatura+bs4, 耗时略超15s(LLM瓶颈) |

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
  KAIWU.md 记忆写入
```

## 文件结构

```
kaiwu/
├── pyproject.toml
└── kaiwu/
    ├── cli/main.py              # CLI入口 typer+rich
    ├── core/
    │   ├── context.py           # TaskContext 数据类
    │   ├── gate.py              # Gate 分类器
    │   └── orchestrator.py      # 流水线编排器
    ├── experts/
    │   ├── locator.py           # 文件→函数 两阶段定位
    │   ├── generator.py         # 从文件读original，LLM只生成modified
    │   ├── verifier.py          # 语法检查 + pytest 验证
    │   ├── search_augmentor.py  # 6步搜索流水线编排
    │   └── office_handler.py    # MVP stub
    ├── search/
    │   ├── intent_classifier.py # 纯关键词意图分类
    │   ├── query_generator.py   # LLM生成英文query
    │   ├── duckduckgo.py        # DDG HTML scraper (bs4)
    │   ├── quality_filter.py    # 域名黑白名单
    │   ├── content_fetcher.py   # trafilatura/httpx正文提取
    │   └── context_compressor.py# LLM压缩摘要
    ├── llm/llama_backend.py     # llama.cpp + Ollama 双后端
    ├── memory/kaiwu_md.py       # KAIWU.md 项目记忆
    ├── tools/executor.py        # read/write/bash/list/git 工具层
    ├── tests/test_core.py       # 24个单元测试
    └── validation/              # V1/V2/V3 验证脚本 + 结论JSON
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
- [ ] 拉 qwen3-8b 跑完整验证，确认更大模型的提升幅度
- [ ] 多文件修改的端到端验证
- [ ] codegen 流水线验证（纯新代码生成）
- [ ] Windows 兼容性完善（路径分隔符、编码等）
