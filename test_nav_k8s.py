"""真实 k8s 导航验证 — 输出页面发现序列"""
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")

from silkworm.config import LLMConfig
from silkworm.feed.navigator import discover_navigation

LLM = LLMConfig(
    base_url="http://192.168.0.13:8000/v1",
    api_key="Horusgame#123",
    model="Gemma4",
    temperature=0.0,
)


async def main():
    # 1) 无 prompt — 全量导航树
    print("=" * 60)
    print("1. 全量导航树")
    print("=" * 60)
    urls = await discover_navigation(
        "https://kubernetes.io/docs/home/",
        llm_config=LLM,
    )
    print(f"→ {len(urls)} 个 URL\n")
    for u in urls[:10]:
        print(f"  {u}")
    if len(urls) > 10:
        print(f"  ... (共 {len(urls)} 个)")

    # 2) prompt="tutorial" — 只抓教程章节
    print("\n" + "=" * 60)
    print('2. prompt="tutorial"')
    print("=" * 60)
    urls = await discover_navigation(
        "https://kubernetes.io/docs/home/",
        prompt="tutorial",
        llm_config=LLM,
    )
    print(f"→ {len(urls)} 个 URL")
    for u in urls:
        print(f"  {u}")

    # 3) prompt="concepts" (中文字串)
    print("\n" + "=" * 60)
    print('3. prompt="concepts"')
    print("=" * 60)
    urls = await discover_navigation(
        "https://kubernetes.io/docs/home/",
        prompt="concepts",
        llm_config=LLM,
    )
    print(f"→ {len(urls)} 个 URL")
    for u in urls:
        print(f"  {u}")


if __name__ == "__main__":
    asyncio.run(main())
