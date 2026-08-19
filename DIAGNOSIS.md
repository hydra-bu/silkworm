# DIAGNOSIS — silkworm 抓取 GitBook (unsloth.ai/docs) 代码块丢失 & 质检误报

> 诊断日期: 2026-08-19 · 范围: 只诊断 + 报告,不改代码
> 抓取命令: `silkworm feed https://unsloth.ai/docs -s unsloth`
> 缓存: `cocoon/ec5c76045f3a6b78.html` (api 页) / `cocoon/_manifest.json` (235 条记录)

---

## 0. 结论摘要 (TL;DR)

**代码块丢失不是单一根因,而是两条独立路径各自损坏:**

| 路径 | 受影响范围 | 根因 | 症状 |
|---|---|---|---|
| **A. Markdown-as-HTML** (`.md` URL 被当 HTML 页抓取) | **180/182 个 html 记录,142 个输出文件 0 代码块** | markdownify 转义占位符 `_txt_` 中的下划线 → `restore_code` 无法回填 | 代码块整体消失,残留 `%%%%SILKWORMCODEBLOCK\_txt\_N%%` |
| **B. 真 GitBook HTML** (gitbook profile) | `/docs` 等真 HTML 页 | `main_selector="main, article"` 只取 `<main>`,而 14/16 个 `<pre>` 在 `<main>` **之后** 的 RSC 隐藏 div 中 | 16 个代码块只剩 2~4 个 |

**质检误报根因:** `check_code_integrity` 用 `count("```") % 2 == 0` 判定 — 0 个代码块(偶数)直接通过;`evaluate()` 又允许 `all_passed or total_score >= 0.6` 双通道放行。

**Boilerplate 漏网根因:** `pipeline.py:44-47` 对 **所有 markdown 源强制 generic profile**,gitbook profile 里已配置的 `div.sr-only`、`"For the complete documentation index"`、`"Last updated"`、`"Previous"/"Next"`、`markdown_strip_headings=["Agent Instructions"]` 全部失效;且 generic profile 的 remove_selectors 本身就不覆盖 GitBook 的 Tailwind 结构。

**附带的第三个 bug:** `_LIQUID_TAG` 正则 (filters.py:756) 要求 `{%` 后紧跟标签名,而 GitBook 输出 `{% columns %}` 带空格 → 50 行 liquid 标签漏网。

---

## 1. 时间线澄清 (先对齐事实)

代码文件修改于 21:30-21:36 (未提交 WIP: gitbook fingerprint/profile、normalize_markdown、markdown 源路由、`.md` 端点抓取),silk 输出生成于 21:42 — **输出是 WIP 代码之后生成的**。

**当前磁盘状态与任务描述存在出入,必须先澄清:**

- `silk/unsloth/docs/basics/api.md` (源: `.../basics/api`, markdown 源) — **代码块完好**: 34 行 ``` 围栏,`Open a terminal and load a GGUF model:` 后是完整的 ` ```bash unsloth run --model ... ` 块 (api.md 第 183-187 行),export 命令也在 (第 414-419 行)。**任务证据 #2 描述的 api.md 症状在当前文件上不存在。**
- `silk/unsloth/docs/basics/api.md.md` (源: `.../basics/api.md` URL, html 源) — **代码块全部丢失**: 0 个 ``` 围栏,17 个 `%%%%SILKWORMCODEBLOCK\_txt\_N%%` 占位符残留。任务证据 #2 的症状 ("Open a terminal" 后代码丢失、export 丢失、`\*` 转义) **精确匹配这个文件**。

结论: 用户观察到的 api.md 代码丢失,实际对应的是 **html 源路径 (api.md.md)** 或更早的 committed 状态。当前 markdown 源 api.md 是好的 — 但 **180 个 html 源记录全部是坏路径**,问题规模远大于单页。

---

## 2. Q1: 代码块在哪一环被丢弃?

### 2.1 路径 A — Markdown-as-HTML (占位符恢复失败) — **主根因**

**抓取拓扑根因 (上游):** `cocoon/_manifest.json` 中 235 条记录里 182 条是 html 源,其中 **180 条的 URL 以 `.md` 结尾**。这些 `.md` 端点本身被爬虫当作独立页面发现并抓取,内容其实是 markdown 文本,却走了完整 HTML 清洗管线 → 全部产出 `xxx.md.md` 双扩展名损坏文件。`resolve_output_path` 对 `.md` URL 又追加 `.md` 造成双扩展名 (cli.py)。

**清洗管线根因 (下游, filters.py):**

1. `filter_protect_code` (filters.py:199-321) 中 `_FENCED_CODE_PATTERN` (line 129-131) 用 `_fence_replacer` (line 312) 把文本中的原始 ``` 围栏替换为占位符:
   ```python
   placeholder = f"{_CODECOOK_PREFIX}_txt_{idx[0]}%%"   # 含下划线 _txt_
   ```
   这与 line 17 注释 "前缀不含 _,避免 markdownify 转义" 的约定**自相矛盾** — 前缀 `%%%%SILKWORMCODEBLOCK` 没有下划线,但 `_txt_` 段重新引入了下划线。

2. `filter_html_to_md` (filters.py:464) 调用 markdownify。**markdownify 会转义文本中的下划线**。已实测:
   ```
   markdownify('%%%%SILKWORMCODEBLOCK_txt_3%%') → '%%%%SILKWORMCODEBLOCK\_txt\_3%%'
   ```
   而 `%%%%SILKWORMCODEBLOCK3%%` (无下划线) 原样通过。

3. `filter_restore_code` (filters.py:324-344) 用 `_code_block_store` 的 key (未转义版) 做 `result.replace(placeholder, ...)` — 但输出里已经是 `\_txt\_`,key 不匹配 → **占位符永远无法回填,代码块丢失**。

**证据:** `api.md.md` 0 围栏 + 17 占位符;`amd.md.md` 6 占位符;全 silk 目录 172 个文件含残留占位符,其中 **142 个文件 0 代码块且 ≥4 占位符**。直接对原始 .md 跑 html 管线模拟: 2 围栏幸存,8 占位符泄漏。

### 2.2 路径 B — 真 GitBook HTML (main_selector 误伤 RSC 结构)

GitBook (Next.js) 服务端渲染 DOM 中,**正文代码块大部分不在 `<main>` 里**:

- `<main>` 范围 258227..509406,内部只有 **2/16 个 `<pre>`** (offset 311033, 315724 — 都是 install.sh 安装脚本)。
- 另外 **14/16 个 `<pre>`** (offset 556405-599570) 在 `</main>` **之后**,位于 `<div hidden id="S:3">` (offset 554201) — Next.js RSC streaming suspense fallback 容器,由 `<script>$RC("B:2","S:2")</script>` 驱动。
- "Open a terminal and load a GGUF model:" 段落 (offset 393774, 在 main 内) 后紧跟 `<!--$?--><template id="B:4"></template><!--/$-->` RSC suspense 边界 — **该代码块根本不在服务端 DOM 里**,只在 flight payload 中。
- `export ANTHROPIC_BASE_URL` 在 main 内 (offset 492752) 作为内联文本存活,但真正的代码块内容 (offset 597069) 在隐藏 div 中。

**根因代码:** `filter_main_content` (filters.py:21-43) 对 `main_selector="main, article"` (detector.py:130) 执行 `elements[0]` 只取**第一个匹配元素** (line 32),把整个 `<main>` 序列化后丢弃其余一切 — 隐藏 div 里的 14 个代码块在此被永久丢弃。`filter_scripts` (line 71-82) 只删 script/style 等,不会删 `div[hidden]`;但 main_content 已经把它们排除在外了。

**证据:** 真 HTML 跑 gitbook profile → 16 pre → main_content 后只剩 2 pre → 最终输出 4 个围栏 (2 个在 main 内的 pre + 2 个内联多行 `<code>`)。`unsloth run --model` 内容完全缺失。

### 2.3 顺带确认: feed/navigator/extractor.py **不是**元凶

`extract_nav_html` (extractor.py, 70 行) 只用 NAV_SELECTORS 提取导航容器做链接发现,与正文/代码块无关。

---

## 3. Q2: 幸存代码块为什么是 "```\n\n<code>\n```"?

两种独立缺陷叠加:

### 3.1 无语言标注 — `_detect_code_language` 只认 `language-*` class

`_detect_code_language` (filters.py:357-372) 只检查 pre/code/父元素的 `language-*` class。GitBook 的 `<pre>` 结构是 **纯 Tailwind utility class,没有任何 `language-*`**:

```html
<pre class="relative overflow-auto py-2.5 text-tint-strong print:overflow-visible border border-tint-subtle ...">
  <code class="table max-h-full w-fit min-w-full [counter-reset:line] ...">
    <span class="highlight-line"><span class="highlight-line-content">CODE<!-- -->\n</span></span>
  </code>
</pre>
```

→ 返回 `""` → `_make_fenced_block("", ...)` (filters.py:190-196) 生成裸 ```。语言信息只存在于 flight payload 的 `"data":{"syntax":"bash"}`。

### 3.2 围栏后空行 — `filter_restore_code` 的正则无差别命中开头围栏

filters.py:352:
```python
result = re.sub(r'(?<=```)\n(?=[^\n])', r'\n\n', result)
```
注释声称 "确保每个**闭合**围栏后有空行",但正则 `(?<=```)\n(?=[^\n])` **不区分开/闭围栏** — 对 ```` ```\ncode ```` (开头围栏 + 代码) 同样命中,插入空行 → ```` ```\n\ncode ````。实测输出 `'```\n\ncurl -fsSL https://unsloth.ai/install.sh | sh\n'` 与 `'```\n\nirm https://unsloth.ai/install.ps1 | iex\n'` 证实。

---

## 4. Q3: flight payload 是否更可靠? 解析路径

**是。** 对 GitBook (Next.js RSC) 页面,flight payload 是**唯一**包含全部代码块 + 语言标注的来源,且不依赖服务端渲染是否完成。

### 4.1 证据

- 全部 200 个 `self.__next_f.push` chunk 集中在 **同一行** (line 261, 639,518 字符,单个 `<script>` 内)。
- 代码块节点: `"type":"code"` 出现于 11 个 chunk;`code-line` 节点 65 处。
- 16 个 `<pre>` 的内容全部能在 payload 中找到 (含不在 DOM 的 "Open a terminal" 块)。

### 4.2 解析路径

```html
<script>self.__next_f.push([1,"12b:[\"$\",\"$16\",\"FaT2L7sWDFMo\",{...,\"block\":{\"object\":\"block\",\"type\":\"code\",\"isVoid\":false,\"data\":{\"syntax\":\"bash\"},\"nodes\":[{\"object\":\"block\",\"type\":\"code-line\",...,\"nodes\":[{\"object\":\"text\",\"leaves\":[{\"object\":\"leaf\",\"text\":\"unsloth run --model ...\",\"marks\":[]}],\"key\":...}]}]}}...])</script>
```

1. **定位**: 匹配 `<script>self.__next_f.push(...)`;每个 chunk 形如 `[1,"<hexlen>:<json>"]` — 第一个元素固定 `1`,第二个是字符串,以 **hex 长度前缀** (如 `12b:`) 开头,后接 JSON。
2. **解码**: 取字符串参数 → 剥掉 `<hexlen>:` 前缀 → JSON 解析。注意 payload 是**双重转义** (源码里 `\\` → 实际 `\`,源码 `\"` → 实际 `"`)。
3. **遍历**: 在 flight tree 中找 `block.type == "code"` 的节点:
   - 语言 = `block.data.syntax`
   - 代码行 = `block.nodes[]` (每个是 `code-line` block) → 每行 `nodes[0].leaves[0].text`
   - 多行代码按节点顺序 join `\n`
4. **保序**: 各行按 `code-line` 数组顺序;代码块按 chunk 内出现顺序 / RSC 引用 id 顺序。

### 4.3 修复建议方向

优先尝试**更简单的替代方案**: GitBook 官方 `.md` 端点 (fetcher 已实现 `_markdown_endpoint`,53 条记录已走此路且代码完好) — 修复应聚焦"别让 .md 内容走 HTML 管线" (见 §7.1)。flight payload 解析作为 `.md` 端点不可用时的兜底 (fallback),在 `filter_protect_code` 之前注入合成 `<pre class="language-X"><code>` 或直接填入 `_code_block_store`。

---

## 5. Q4: boilerplate 漏网 — remove_selectors 没覆盖 vs 被当正文?

**两者都有,但主因是 profile 选择错误 + generic profile 覆盖不全。**

### 5.1 主因: markdown 源强制 generic (pipeline.py:44-47)

```python
if record.source == "markdown":
    record.framework = "generic"   # ← gitbook 专属配置全部失效
```

fetcher 在决定抓 `.md` 端点前**已经**对 HTML 做过 `detect_framework` (=gitbook,6 分 ≥ 阈值 4),但这个结果没有传给 record (manifest 里 framework=None),pipeline 重新强制 generic。gitbook profile 里已配置的:
- `remove_selectors` 含 `div.sr-only` (detector.py:134)
- `boilerplate_phrases` 含 `"For the complete documentation index"`/`"Last updated"`/`"Previous"`/`"Next"` (detector.py:136-142)
- `markdown_strip_headings=["Agent Instructions"]` (detector.py:143)

**全部对 markdown 源失效。** generic profile (detector.py:146-163) 没有这些。

### 5.2 逐项确认 + GitBook 准确 CSS 选择器

| 泄漏项 | 出现位置 | GitBook DOM 结构 | 准确 CSS 选择器 | generic 为何漏 |
|---|---|---|---|---|
| "For the complete documentation index...llms.txt" | 每页顶部 (api.md L7, 224 个文件) | `<main>` **第一个子元素** `<div class="sr-only">...` | `div.sr-only` | generic 无此 selector;gitbook 有但没用到 |
| "Last updated X days ago" | 页尾 | `<div class="max-w-3xl ... mt-6 flex flex-row flex-wrap items-center gap-4 text-tint"><p class="mr-auto text-sm">Last updated <time ...>` | `p.mr-auto` (含 `<time>`),或 `p:has(> time)` | generic phrases 无 "Last updated" |
| "[Previous...][Next...]" 导航 | 页尾 | `<div class="max-w-3xl ... flex flex-col md:flex-row mt-6 gap-2 text-tint"><a class="group text-sm p-2.5 ..."><span class="text-xs">Previous</span>...` — **无 `<nav>` 包裹,纯 div+a+span** | `div[class*='md:flex-row'][class*='mt-6']`,或 `a[href^='/docs/']:has(span.text-xs)` | generic remove_selectors 只有 nav/header/footer/aside,全部落空 |
| 侧边栏 tab 链接卡片 (install.md L49) | install.md 等 7 文件 | `<table data-view="cards">` 内含 11 个 `<a href="/pages/<id>">` content-ref 链接 | `table[data-view="cards"]` | 无任何 profile 覆盖;normalize_markdown 也不处理原始 HTML 表格 |
| `# Agent Instructions` 章节 | 221 个文件 | markdown 源正文尾部 | — (markdown_strip_headings) | 仅 gitbook profile 有 `["Agent Instructions"]` |

### 5.3 附带的 liquid 标签 bug (filters.py:756)

```python
_LIQUID_TAG = re.compile(r"^\s*\{%/?(columns|column|...)\}.*$")
```
正则要求 `{%` 后**紧跟**标签名,而 GitBook 输出 `{% columns %}` 带空格 → **永不匹配** (已实测所有变体均返回 False)。api.md 泄漏 49 行 `{%` 标签 (api.md.md 50 行)。

---

## 6. 质检为何误报 (quality.py)

- `check_code_integrity` (quality.py:50-52): `passed = count % 2 == 0` — **0 个代码块 = 0 = 偶数 = 通过**。
- `check_code_block_content` (quality.py:63+): 只查块内 `\_` 转义和 Python 缩进丢失 — 对"整块丢失"无感知。
- `check_noise_lines` (quality.py:169+): NOISE_PHRASES 阈值 ≤5 次 + 链接密度,泄漏少量导航行时可通过。
- `evaluate()` (quality.py:395, 412-413): `passed = all_passed or total_score >= 0.6` — 长文页即使代码全丢,靠长度/低噪声分数也能 ≥0.6 双通道放行。

**净效果:** 长文 + 0 代码块 + 少量噪音 = 全绿。"54 页全部通过质检" 是误报。

---

## 7. 修复方案 (按优先级,代码本轮未改)

### 7.1 [最高] 消除 Markdown-as-HTML 坏路径 — 180 页

- **fetcher.py** `fetch_page` / 爬虫链接去重: 跳过或以规范化 URL 去重 `.md` 结尾的链接 (它们是同页 markdown 端点,不是独立页)。或对 html 源记录在进入管线前检测内容形态 (无 HTML 标签 / 以 `> ` 或 `# ` 开头) → 标记为 markdown 源。
- **pipeline.py** `run()` (line 44-47): markdown 源不要强制 generic — 应**继承 fetcher 阶段 detect_framework 的结果** (fetcher 调用 `_markdown_endpoint` 前已检测出 gitbook,把结果存入 `record.framework`),或对 markdown 源复用同一检测逻辑。
- **filters.py** `_fence_replacer` (line 312): 占位符去掉下划线 — `f"{_CODECOOK_PREFIX}txt{idx}%%"`,与 line 17 注释约定一致;并在 `filter_restore_code` (line 324) 增加防御性替换 `\_txt\_` → 回填 (双保险)。
- **cli.py** `resolve_output_path`: 对 `.md` 结尾 URL 去掉多余 `.md` 扩展名,消除 `xxx.md.md`。

### 7.2 [高] 真 GitBook HTML 路径 — main_selector 误伤

- **detector.py** gitbook profile `main_selector` (line 130): 改为 `main, article, div[hidden]` — 或在 `filter_main_content` 增加 profile 选项 `include_hidden_blocks`,把 `<div hidden>` 中 `<pre>` 并入 main 内容 (注意去重,避免 RSC 隐藏内容与主内容重叠)。
- **filters.py** `filter_main_content` (line 21-43): 由 `elements[0]` 只取第一个,改为可配置 `merge_matches` — gitbook 场景合并所有匹配元素的 `<pre>`。
- **filter_protect_code** 前置: 对 gitbook profile 增加 flight payload 兜底解析 (§4),把 payload 中 `type=="code"` 的块注入为 `<pre class="language-{syntax}">`,确保 suspense 边界后的代码块不丢。

### 7.3 [中] "```\n\n<code>" 格式

- **filters.py** line 352-353: 把无差别正则改为只作用于**闭合围栏** — 例如匹配 `\n```(?=\s*$)` 后补空行,或跟踪围栏配对状态 (open/close 计数器) 再插入;最简单可靠: 删掉 line 352-353 的全局正则,改为在 `filter_restore_code` 的 `replace` 包裹处 (line 344) 已保证 `\n\n{code}\n\n` 的前提下,只对缩进列表场景 (line 338-339) 的替换结果补 `\n\n` 后缀。
- **filters.py** `_detect_code_language` (line 357): 增加 flight payload `data.syntax` 注入 (见 §7.2),或在 gitbook profile 增加 `code_lang_extractor` 钩子 (从 `highlight-line` 结构的父级或 payload 取语言)。

### 7.4 [中] Boilerplate 漏网

- **detector.py** generic profile (line 146-163): 增加通用 GitBook 项 —
  - `remove_selectors` += `"div.sr-only"`, `"p.mr-auto"`, `"table[data-view='cards']"`, `"div[class*='md:flex-row'][class*='mt-6']"`
  - `boilerplate_phrases` += `"For the complete documentation index"`, `"Last updated"`, `"Previous"`, `"Next"`
  - `markdown_strip_headings` += `["Agent Instructions"]`
  - 或更彻底: 直接修正 §7.1 的 profile 选择,让 gitbook profile 真正生效 (gitbook profile 已含这些配置,只需别被强制 generic 覆盖)。
- **normalize_markdown** (filters.py:759+): 增加对原始 HTML 卡片表格 `<table data-view="cards">...</table>` 的整块剥离 (正则或解析);修复 `_LIQUID_TAG` (line 756) 为 `\{%\s*/?(...)` 允许空格。

### 7.5 [中] 质检误报

- **quality.py** `check_code_integrity` (line 50-52): 改为 `count > 0 and count % 2 == 0` (有代码块才谈完整性),并将 `%%%%SILKWORMCODEBLOCK` 残留占位符纳入失败条件。
- **quality.py** `evaluate()` (line 412-413): 增加硬性失败项 — 若原始内容含代码 (html 源含 `<pre>` / markdown 源含 ``` 围栏) 而输出 0 围栏,直接 `passed=False` (防止双通道放行)。可在 PipelineResult 传入 `raw_code_count` 上下文。

### 7.6 [低] 爬虫侧

- 链接发现层对 `.md` 端点去重 (同页双记录: `.../basics/api` 与 `.../basics/api.md` 各产生一份输出,前者好后者坏,浪费 2 倍处理量)。

---

## 8. 证据清单

| 证据 | 位置 |
|---|---|
| 16 `<pre>`,仅 2 个在 `<main>` 内,14 个在 `</main>` 后 `<div hidden id="S:3">` | cocoon/ec5c76045f3a6b78.html (main 258227-509406; pre 311033/315724 vs 556405-599570; S:3 @ 554201) |
| "Open a terminal" 段后是 RSC suspense 边界 `<!--$?--><template id="B:4">` | 同文件 offset 393774 |
| GitBook pre 纯 Tailwind class,无 language-* | 同文件 (pre 结构) |
| flight payload 200 chunks 单行,`type:"code"` + `data.syntax` + `code-line` 节点 | 同文件 line 261 (639KB);chunk 123/124 含完整 bash 代码 |
| 占位符下划线被 markdownify 转义 | 实测: `markdownify('%%%%SILKWORMCODEBLOCK_txt_3%%')` → `\_txt\_3%%` |
| api.md.md 0 围栏 + 17 占位符;全 silk 172 文件占位符,142 文件 0 代码块 | silk/unsloth/docs/basics/api.md.md 等 |
| markdown 源 api.md 代码完好 (34 围栏) | silk/unsloth/docs/basics/api.md L183-187, L414-419 |
| 180/182 html 记录 URL 以 .md 结尾 | cocoon/_manifest.json |
| 4 项 boilerplate 泄漏在 generic 路径全部出现 | 真 HTML 跑 generic profile 实测输出 |
| install.md 卡片表格 `/pages/<id>` 11 链接 | silk/unsloth/docs/get-started/install.md L49 |
| `_LIQUID_TAG.match("{% columns %}")` → False | 直接 Python 实测 |
| 0 代码块 = 偶数 = 质检通过 | quality.py:50-52, 412-413 |

---

## 9. 一句话总结

代码块丢失的**主根因**是爬虫把 180 个 `.md` 端点当 HTML 页抓取 + `_txt_` 占位符被 markdownify 转义导致恢复失败 (路径 A);**次根因**是 gitbook profile 的 `main_selector` 只取 `<main>`,丢弃了位于 RSC 隐藏 div 中的 14/16 代码块 (路径 B);质检误报源于"0 个代码块也算偶数通过"的完整性判定;boilerplate 漏网源于 markdown 源被强制 generic profile,gitbook 专属清洗配置 (sr-only、短语、Agent Instructions 章节剥离) 全部未生效。