# Silkworm — 站点文档抓取工具设计方案

> *Feed it pages, get back silk.* 喂给它网页，吐出来的是丝。
>
> 目标：将任意文档站点（K8s/Docker/GitLab/MkDocs/Docusaurus/Sphinx 等）批量采集、清洗为高质量本地 Markdown，支持增量更新、离线可用。

**命名隐喻**：蚕吃桑叶（原始网页）、消化转化（清洗管线）、吐丝成茧（本地整洁的 Markdown 文档库）。全流程和 CLI 命令均沿用这条隐喻线，便于统一话语体系。

| 隐喻 | 对应阶段 | CLI 命令 |
|---|---|---|
| 🍃 Feed（喂食） | Stage 1-2 发现 + 原始抓取 | `silkworm feed <url>` |
| 🧵 Spin（吐丝） | Stage 3-5 框架识别 + 清洗 + 质检 | `silkworm spin <site>` |
| 🏠 Cocoon（茧） | 原始 HTML 永久存储区 | `silkworm cocoon list` |
| ✨ Silk（丝） | Stage 6 最终 Markdown 产物 | 输出目录 `silk/` |

---

## 0. 设计原则

1. **框架识别优先于站点识别**：新增站点不写"专用爬虫"，只在必要时补一个 override 规则。
2. **原始数据不可变**：HTML 原始副本 + 元数据永久保留，所有清洗都在副本上做，可无损重跑。
3. **Filter 单一职责 + 可插拔**：每个清洗步骤独立、可单测、可按需跳过。
4. **规则先行，LLM 判疑难**：LLM 只处理规则无法判定的边界案例，控制成本。
5. **产出物本地可用**：图片本地化、站内链接重写为相对路径，脱离原站也能浏览。
6. **增量优先**：基于内容哈希，避免重复抓取/重复处理。

---

## 1. 技术栈建议

| 模块 | 推荐库 | 说明 |
|---|---|---|
| 语言 | Python 3.11+ | 生态最全，爬虫/解析/NLP 库丰富 |
| HTTP 抓取 | `httpx` (异步) | 支持 HTTP/2、连接池 |
| JS 渲染兜底 | `playwright` | 仅对检测为 SPA 的页面启用，避免全量拖慢 |
| HTML 解析 | `lxml` / `selectolax` | selectolax 性能更好，可选 |
| 正文提取兜底 | `trafilatura` | 优先于 readability，对文档站效果更稳定 |
| HTML→MD | `markdownify` 或自研（基于 lxml 遍历） | 需要定制代码块/表格处理，建议自研核心转换器 |
| 存储 | SQLite（元数据/状态） + 文件系统（HTML/MD 原文） | 单机场景足够，无需引入数据库服务 |
| 任务调度 | `asyncio` + 信号量限速 | 按域名独立并发控制 |
| CLI | `typer` | 命令行体验好 |
| 配置 | `pydantic` + YAML | 站点配置 schema 化，减少手写 JSON 出错 |

---

## 2. 整体架构

```
CLI/Config
    │
    ▼
┌─────────────────────────────────────────────┐
│ 🍃 FEED · Stage 1: Discovery（站点发现）        │
│  - sitemap.xml 解析 / 导航树 BFS              │
│  - URL 规范化（去#/尾部/排序 query/协议统一）  │
│  - 内容指纹去重（SHA256 相同→合并）             │
│  → 待抓取队列                                 │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│ 🍃 FEED · Stage 2: Fetch（原始抓取）           │
│  - robots.txt 解析 + 缓存（优先尊重）           │
│  - 信号量限速（按域名独立并发池）                │
│  - httpx 请求，SPA 检测→ playwright 兜底        │
│  - 失败重试（指数退避，≤3 次，仅重试可恢复错误） │
│  - 保存原始 HTML + 响应元数据 → 🏠 Cocoon 存储区 │
│  - 内容哈希 → 判断是否需要重新处理（增量）        │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│ 🧵 SPIN · Stage 3: Framework Detection（框架识别）│
│  - 指纹打分：meta generator / 特征 class / JS 全局变量 │
│  - 命中 → 加载框架 Profile；未命中 → 通用 Profile │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│ 🧵 SPIN · Stage 4: Cleaning Pipeline（清洗管线） │
│  Filter 1: 主区域提取（selector / trafilatura 兜底）│
│  Filter 2: 残留导航兜底清除（防御性，非必跑）      │
│  Filter 3: 样板文字清除（Feedback/Edit this page）│
│  Filter 4: 脚本/嵌入清除                        │
│  Filter 5: 代码块处理（语言映射表，不猜测）        │
│  Filter 6: HTML→Markdown 转换                  │
│  Filter 7: 资源本地化（图片下载/链接重写）         │
│  Filter 8: Override 规则（站点特例修补）          │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│ 🧵 SPIN · Stage 5: Quality Gate（质量控制）      │
│  - 规则打分（长度/代码闭合/标题跳跃/噪声短语库）    │
│  - 边界情况 → LLM 复核（仅疑难案例）              │
│  - 通过 → 输出                                 │
│  - 不通过 → 分析失败 Filter → 调整参数重试(≤2次)  │
│  - 仍不通过 → 落盘失败报告 + 原始 HTML → 人工介入  │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│ ✨ SILK · Stage 6: Output（输出组织）           │
│  - 镜像站点导航层级的目录结构                     │
│  - frontmatter（源 URL/抓取时间/内容哈希）        │
│  - 生成采集报告（成功/失败/待人工列表）            │
└─────────────────────────────────────────────┘
```

---

### URL 规范化策略（Stage 1 补充）

发现阶段收集到的 URL 在经过 allow/deny 过滤后，在入队前执行以下规范化步骤：

| 步骤 | 操作 | 示例 |
|---|---|---|
| Fragment 移除 | 删除 `#` 部分 | `/docs/foo#overview` → `/docs/foo` |
| Trailing slash 归一 | 统一移除尾部 `/`（除非 path 仅为 `/`） | `/docs/foo/` → `/docs/foo` |
| Query param 排序 | 按 key 字母序重排，删除已知无意义 param（如 `utm_*`） | `?b=1&a=2` → `?a=2&b=1` |
| 默认页面归一 | `index.html` / `default.aspx` 等移除 | `/docs/foo/index.html` → `/docs/foo` |
| 协议统一 | `http://` → `https://` | `http://example.com/foo` → `https://example.com/foo` |

规范化完成后，以 `canonical_url` 为唯一键去重。若两个 URL 规范化后相同，保留第一个出现的，后续丢弃并记入日志。

**内容指纹去重（补充）**：即使 URL 不同，如果下载后的原始 HTML 的 SHA256 哈希相同，视为重复页面，后续版本跳过再次处理。这能处理 URL 不同但内容完全相同的场景（如 `/docs/v1.0/` vs `/docs/stable/`）。

---

## 3. 关键数据结构（供 Claude Code 实现参考）

```python
# models.py

class SiteConfig(BaseModel):
    base_url: str
    allow_patterns: list[str]        # URL 允许规则（正则）
    deny_patterns: list[str] = []
    crawl_delay: float = 1.0         # 秒
    max_concurrency: int = 4
    user_agent: str = "DocScraper/1.0 (+contact)"
    respect_robots: bool = True
    force_render: bool = False        # 强制走 playwright

class FrameworkProfile(BaseModel):
    name: str                         # mkdocs / docusaurus / sphinx / generic
    fingerprint_rules: list[str]      # 用于检测
    main_selector: str                # 主内容 CSS selector
    remove_selectors: list[str]       # 需要剔除的元素
    boilerplate_phrases: list[str]    # "Was this page helpful" 等
    code_lang_class_map: dict[str, str]  # class -> 语言名映射

class PageRecord(BaseModel):
    url: str
    canonical_url: str
    fetched_at: datetime
    content_hash: str
    http_status: int
    raw_html_path: str
    framework: str
    status: Literal["pending", "fetched", "cleaned", "passed", "failed", "manual_review"]
    quality_report: dict | None = None
```

---

## 4. 框架识别机制（Stage 3 核心）

用**打分制**而非二元匹配，避免单一特征误判：

```python
FINGERPRINTS = {
    "mkdocs-material": [
        ('meta[name="generator"]', "mkdocs", 3),
        ('.md-content', None, 2),
        ('.md-nav', None, 1),
    ],
    "docusaurus": [
        ('meta[name="generator"]', "docusaurus", 3),
        ('#__docusaurus', None, 3),
    ],
    "sphinx": [
        ('.rst-content', None, 2),
        ('div[role="navigation"].sphinxsidebar', None, 2),
    ],
    # ... 可持续扩展
}
# 每个框架累计得分，取最高分且超过阈值的作为匹配；
# 否则 fallback = "generic"（走 trafilatura 兜底 + 通用规则）
```

**新增站点的成本应该是**：写一个 `SiteConfig`（allow_patterns + crawl_delay），框架大概率已被识别，**无需任何代码改动**。只有遇到框架内的"该实例特有噪声"才补一条 override 规则。

### Override 规则格式

Filter 8 的 Override 规则按站点粒度配置，格式如下：

```python
class OverrideRule(BaseModel):
    site_match: str                                # base_url 匹配（支持前缀）
    selector_remove: list[str] = []                # 额外剔除的 CSS selector
    selector_patch: list[tuple[str, str]] = []     # (selector, replacement_html) 替换指定区域
    main_selector_override: str | None = None      # 覆盖该站点的主内容选择器
    pre_clean_hooks: list[str] = []                # 在 Filter 1 前执行的 hook 函数名
    boilerplate_add: list[str] = []                # 该站点特有的噪声短语

    # 适用场景举例
    # site_match="docs.gitlab.com", selector_remove=["div.sharing-links"]
    # site_match="kubernetes.io", main_selector_override="main#docsContent"
    # site_match="example.com/docs", selector_patch=[("div.outdated-banner", "")]
```

**设计原则**：Override 是白名单机制，每条规则必须明确匹配站点。不设通配/模糊匹配，避免规则泄漏到不应该应用的站点。

### 代码语言映射表示例

框架 Profile 中的 `code_lang_class_map` 用于将 HTML 代码块的 CSS class 转为 Markdown fence 语言标识：

```python
FRAMEWORK_LANG_MAPS = {
    "mkdocs-material": {
        "language-python": "python",
        "language-javascript": "javascript",
        "language-go": "go",
        "language-yaml": "yaml",
        "language-bash": "bash",
        "language-json": "json",
        "language-rust": "rust",
        "language-java": "java",
        "language-typescript": "typescript",
        "language-html": "html",
        "language-sql": "sql",
    },
    "docusaurus": {
        "code-block__language-python": "python",
        "code-block__language-js": "javascript",
        "code-block__language-go": "go",
        # Docusaurus 也常直接用 language-xxx 前缀
        "language-python": "python",
        "language-js": "javascript",
    },
    "sphinx": {
        "highlight-python": "python",
        "highlight-javascript": "javascript",
        "highlight-default": "python",
        "highlight-none": "",           # 无语言→留空 fence
    },
}
```

映射规则：先精确匹配，再前缀匹配。都匹配不到就留空 fence（```），**不做启发式猜测**。

---

## 5. 质量控制的具体规则

| 检查项 | 规则 | 说明 |
|---|---|---|
| 长度 | 正文 ≥ 500 字符（可配置） | 过短大概率提取失败 |
| 代码完整性 | ``` 计数为偶数 | 简单但有效 |
| 标题跳跃 | 检测**异常跳跃**（如连续多次 h1→h4） | 不要求严格连续，允许合理跳级 |
| 噪声行 | 短语库匹配 + 链接密度双判定 | 短语库可维护列表，如 "Edit this page"/"反馈"等 |
| 综合打分 | 加权分数，落在灰色区间才送 LLM | 明确通过/不通过的不调用 LLM |

**失败重试闭环**：

- 质量报告需指出**具体哪个 Filter 可能有问题**（如"Filter 1 主区域选择器可能选大/选小"）
- 重跑时基于报告调整策略（如切换到 trafilatura 兜底而非站点 selector）
- 设置最大重试次数（建议 2 次），每次重试前检查**上次失败的具体 Filter 是否已调整**，避免相同参数空转
- 若第 1 次失败原因是"长度不足"，第 2 次尝试切换到 trafilatura 兜底提取
- 若第 1 次失败原因是"代码闭合异常"，第 2 次尝试使用更保守的代码块提取（fence 内部空白字符不做转义）
- 若第 2 次仍失败，落盘原始 HTML + 失败报告 + 重试历史，标记 `manual_review`，**不进入无限循环**
- 同一站点的重试记录积累到一定数量（如 ≥5 条），自动建议为该站点配置 Override 规则或切换检测 Profile

**Fetch 层网络重试（与质检重试独立）**：

- 仅重试**可恢复**的错误：`timeout`、`connection error`、`5xx`（4xx 不重试，直接标记失败）
- 策略：指数退避，初始等待 1s，每次 ×2，上限 3 次
- 每次重试更换不同的 User-Agent 轮转，降低被拦截概率
- 超过 3 次的页面计入采集报告"失败"列表，不阻塞整体流程

---

## 6. 资源本地化（容易被忽略但很关键）

- 图片：下载到 `assets/<page-slug>/`，Markdown 中改为相对路径
- 站内链接：抓取范围内的页面互链，重写为本地相对路径；范围外的链接保留原始绝对 URL
- 代码块语言：仅通过映射表识别，识别不到就留空 fence（```），不做启发式猜测

---

## 7. 输出目录结构示例

```
silk/                     # ✨ 最终产物：整洁的 Markdown 文档库
  <site-name>/
    docs/
      getting-started/introduction.md
      concepts/architecture.md
    assets/
      getting-started_introduction/diagram.png
    _meta/
      pages.db          # SQLite: PageRecord 表
      report.md         # 本次采集报告
cocoon/                   # 🏠 原始 HTML 永久存储区（不可变）
  <site-name>/
    <url-hash>.html
```

每篇 Markdown 头部 frontmatter：

```yaml
---
title: Introduction
source_url: https://kubernetes.io/docs/concepts/...
fetched_at: 2026-07-09T10:00:00Z
content_hash: sha256:xxxx
framework: mkdocs-material
---
```

`title` 从 HTML `<title>` 标签或页面 `<h1>` 提取；若两者都不可得，用 URL 最后一段路径片段作为兜底。

**注意**：`silk/` 和 `cocoon/` 目录下的所有文件均为运行时生成产物，应加入 `.gitignore` 避免污染版本库。推荐在项目根目录预置 `.gitignore`：

```
# 运行时生成产物
silk/
cocoon/
# 测试临时目录
/tmp/silkworm-test/
# Python 缓存/报告
.coverage
htmlcov/
.pytest_cache/
*.pyc
```

---

## 8. 建议的开发路线图（分阶段实现，方便逐步喂给 Claude Code）

**MVP（第一批实现）—— 先让蚕能吃、能吐丝**
1. 🍃 Feed：单站点 sitemap 发现 + httpx 抓取 + 原始 HTML 落盘至 `cocoon/`
2. 🧵 Spin（框架识别）：先支持 MkDocs + generic 兜底两种
3. 🧵 Spin（清洗）：Filter 1/3/4/5/6（主区域提取、样板清除、代码处理、MD 转换）
4. 质检：仅规则校验（跳过 LLM）
5. ✨ Silk：基础输出 + frontmatter

**第二批**
6. Filter 7 资源本地化（图片下载、链接重写）
7. 增量更新（内容哈希对比，跳过未变化页面）
8. SPA 检测 + playwright 兜底

**第三批**
9. LLM 边界案例复核
10. 更多框架 Profile（Docusaurus/Sphinx/GitBook）
11. 失败重试闭环 + 人工介入队列
12. CLI 完善（进度条、断点续爬、报告可视化）

---

## 9. 给 Claude Code 的实现建议

把本文档拆分为上述三批，**每批单独作为一次任务提交给 Claude Code**，并要求：

- 先写 `models.py`（数据结构）+ 单元测试骨架
- 每个 Filter/Stage 独立文件，便于单测
- 先跑通 MVP 单站点全流程（哪怕只支持 MkDocs），再横向扩展框架支持
- 每完成一批，人工用 1-2 个真实文档站验证效果后再进入下一批

这样可以避免一次性让 Claude Code 生成过大代码量导致上下文丢失或错误累积。
