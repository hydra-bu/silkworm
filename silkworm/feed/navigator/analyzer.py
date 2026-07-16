"""LLM 导航树分析 — 发送 nav HTML 到 LLM，解析为结构化 JSON 树。"""

import json
import logging

from openai import OpenAI

from silkworm.config import LLMConfig

logger = logging.getLogger(__name__)

# 导航分析系统提示词
NAV_SYSTEM_PROMPT = """You are a navigation tree analyzer. Given the HTML of a documentation sidebar navigation, extract the navigation structure as a JSON tree.

Rules:
1. Output ONLY valid JSON, no markdown fences, no explanation.
2. Every item MUST have "title" (str) and "url" (str, absolute or relative). If an item has children, it should also have "children" (list).
3. If a section heading has no URL, set url to "".
4. Group items hierarchically. Max depth: 3 levels.
5. Ignore: search bars, version switchers, theme toggles, collapse/expand icons.
6. Include ALL navigation links found.
7. Priority classification: For EACH item, add a "priority" field with value "core" or "secondary".
   - "core" = 核心内容 / essential content: 指南(guides), 教程(tutorials), 入门(getting-started),
     概念(concepts), 概述(overview), 设置(setup), 核心功能(main features), 基本概念(fundamentals),
     架构(architecture), 工作负载(workloads), 配置(configuration), 部署(deployment) —
     what users primarily need to learn for the product.
   - "secondary" = 次要内容 / supplementary: 参考(reference), API文档(api), 贡献指南(contribute),
     发布说明(release-notes), 常见问题(FAQ), 术语表(glossary), 变更日志(changelog),
     附录(appendix), 资源(resources), 社区(community), 博客(blog), 视频(videos) —
     auxiliary or reference content.

   Use semantic understanding of the section title and context. The examples above are NOT
   exhaustive — classify based on how important the section is for learning the product's
   core functionality.

Output format:
{"sections": [{"title": "...", "url": "...", "priority": "core", "children": [{"title": "...", "url": "...", "priority": "secondary", "children": [...]}]}]}"""


def analyze_nav_tree(nav_html: str, llm_config: LLMConfig) -> dict | None:
    """发送导航 HTML 到 LLM，返回解析后的导航树 dict。失败返回 None。"""
    api_key = llm_config.get_api_key()
    if not api_key:
        logger.warning("LLM API key 未配置，跳过导航分析")
        return None

    try:
        client = OpenAI(
            base_url=llm_config.base_url,
            api_key=api_key,
        )

        resp = client.chat.completions.create(
            model=llm_config.model,
            messages=[
                {"role": "system", "content": NAV_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Analyze this navigation HTML and return the JSON tree:\n\n{nav_html}",
                },
            ],
            temperature=llm_config.temperature,
            max_tokens=llm_config.max_tokens,
            timeout=llm_config.timeout,
        )

        content = (resp.choices[0].message.content or "").strip()
        return _parse_llm_response(content)

    except Exception as e:
        logger.warning("LLM 导航分析失败: %s", e)
        return None


def _parse_llm_response(content: str) -> dict | None:
    """从 LLM 响应中提取 JSON。"""
    if not content:
        return None

    # 去掉可能的 markdown fences
    content = content.strip()
    if content.startswith("```"):
        # 找到 fence 开始和结束
        start = content.find("\n")
        end = content.rfind("```")
        if start != -1 and end != -1:
            content = content[start:end].strip()
        elif start != -1:
            content = content[start:].strip()
        elif end != -1:
            content = content[:end].strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # 尝试找到 JSON 片段
        start_idx = content.find("{")
        end_idx = content.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            try:
                data = json.loads(content[start_idx : end_idx + 1])
            except json.JSONDecodeError:
                return None
        else:
            return None

    if not isinstance(data, dict) or "sections" not in data:
        return None

    return data
