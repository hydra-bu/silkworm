"""
Silkworm 端到端测试：抓取 Kubernetes 文档。
全流程：发现 → 抓取 → 框架检测 → 清洗 → 质检 → 输出
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import httpx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich import box

from silkworm.feed.discovery import discover_from_sitemap, normalize_url
from silkworm.feed.fetcher import fetch_page
from silkworm.spin.detector import detect_framework, get_profile, BUILTIN_FINGERPRINTS
from silkworm.spin.filters import run_pipeline
from silkworm.spin.quality import evaluate
from silkworm.silk.output import build_frontmatter, resolve_output_path, write_silk

console = Console()
COCOON_DIR = Path("/tmp/silkworm-test/cocoon")
SILK_DIR = Path("/tmp/silkworm-test/silk")
SITE_NAME = "kubernetes"


async def main():
    console.print(Panel.fit("[bold cyan]🐛 Silkworm 端到端测试 — Kubernetes 文档抓取[/]", box=box.HEAVY))

    # ── Stage 0: Config ──
    console.rule("[bold]Stage 0: 配置")
    allow_patterns = [r"/docs/"]
    deny_patterns = [r"/docs/reference/"]
    console.print(f"  allow: /docs/")
    console.print(f"  deny: /docs/reference/")
    console.print(f"  cocoon: {COCOON_DIR}")
    console.print(f"  silk: {SILK_DIR}")
    COCOON_DIR.mkdir(parents=True, exist_ok=True)
    SILK_DIR.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: Discovery ──
    console.rule("[bold]Stage 1: 🍃 发现 (Discovery)")

    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        # Discover from sitemap
        sitemap_url = "https://kubernetes.io/en/sitemap.xml"
        console.print(f"  sitemap: {sitemap_url}")

        all_urls = await discover_from_sitemap(
            client, sitemap_url,
            allow_patterns=allow_patterns,
            deny_patterns=deny_patterns,
        )
        console.print(f"  ✅ 从 sitemap 发现 {len(all_urls)} 个符合条件的 URL")

        # Sample: take first 5 docs pages
        test_urls = all_urls[:5]
        console.print(f"  选取前 {len(test_urls)} 个进行抓取:")
        for i, u in enumerate(test_urls, 1):
            console.print(f"    {i}. {u}")

        # ── Stage 2: Fetch ──
        console.rule("[bold]Stage 2: 📥 抓取 (Fetch)")
        records = []
        for url in test_urls:
            console.print(f"  抓取: {url}")
            record = await fetch_page(client, url, COCOON_DIR)
            records.append(record)
            status_icon = "✅" if record.status == "fetched" else "❌"
            console.print(f"    {status_icon} status={record.http_status}, hash={record.content_hash[:12]}...")
            if record.status == "fetched":
                console.print(f"     raw_html: {record.raw_html_path}")

        # ── Stage 3: Framework Detection ──
        console.rule("[bold]Stage 3: 🔍 框架检测 (Detect)")
        fw_detection_table = Table(title="框架检测结果", box=box.ROUNDED)
        fw_detection_table.add_column("URL", style="cyan")
        fw_detection_table.add_column("检测结果", style="yellow")
        fw_detection_table.add_column("Profile", style="green")
        fw_detection_table.add_column("主选择器", style="white")

        for record in records:
            if record.status != "fetched":
                continue
            raw_path = COCOON_DIR / record.raw_html_path
            if not raw_path.exists():
                continue
            raw_html = raw_path.read_text(encoding="utf-8")
            fw_name = detect_framework(raw_html)
            profile = get_profile(fw_name)
            record.framework = fw_name

            # Show what fingerprints scored
            scores_info = ""
            from lxml import html as lxml_html
            tree = lxml_html.fromstring(raw_html)
            for fw_name_inner, rules in BUILTIN_FINGERPRINTS.items():
                total = 0
                for rule in rules:
                    els = tree.cssselect(rule.selector)
                    if not els:
                        continue
                    if rule.value is not None:
                        for el in els:
                            if rule.value.lower() in (el.get("content", "") or "").lower():
                                total += rule.score
                                break
                    else:
                        total += rule.score
                if total > 0:
                    scores_info += f"  {fw_name_inner}:{total}"

            fw_detection_table.add_row(
                record.url.split("/")[-1] or "home",
                fw_name,
                profile.name,
                profile.main_selector if profile.main_selector else "(无)",
            )
            console.print(f"  {record.url}: → [bold]{fw_name}[/] 得分: {scores_info}")

        console.print(fw_detection_table)

        # ── Stage 4: Cleaning Pipeline ──
        console.rule("[bold]Stage 4: 🧹 清洗管线 (Filters)")
        for record in records:
            if record.status != "fetched":
                continue
            raw_path = COCOON_DIR / record.raw_html_path
            if not raw_path.exists():
                continue

            raw_html = raw_path.read_text(encoding="utf-8")
            profile = get_profile(record.framework)

            console.print(f"\n[cyan]📄 {record.url}[/]")
            console.print(f"    原始 HTML: {len(raw_html)} 字节")

            content, steps = run_pipeline(raw_html, profile)
            step_count = len(steps)
            console.print(f"    执行 {step_count} 个 Filter: [bold]{' → '.join(steps)}[/]")
            console.print(f"    输出内容: {len(content)} 字符")

            # ── Stage 5: Quality Gate ──
            console.rule("[bold]Stage 5: ✅ 质量门禁 (Quality Gate)")
            report = evaluate(content)
            quality_table = Table(title=f"质检报告 — {record.url.split('/')[-1]}", box=box.ROUNDED)
            quality_table.add_column("检查项", style="cyan")
            quality_table.add_column("通过", style="green")
            quality_table.add_column("得分", justify="right")
            quality_table.add_column("详情")

            for check_name, check_result in report.checks.items():
                status_str = "✅ PASS" if check_result.passed else "❌ FAIL"
                quality_table.add_row(
                    check_name,
                    status_str,
                    f"{check_result.score:.3f}",
                    check_result.detail,
                )
            quality_table.add_row(
                "[bold]总分",
                "✅ 通过" if report.passed else "❌ 未通过",
                f"{report.total_score:.3f}",
                report.message,
            )
            console.print(quality_table)

            # Show output sample (first 1000 chars)
            if content:
                preview = content[:1000]
                console.print(Panel(
                    Syntax(preview, "markdown", theme="monokai", word_wrap=True),
                    title=f"📝 Markdown 输出预览 (前 {len(preview)} 字符)",
                    border_style="green",
                ))

            # ── Stage 6: Output ──
            console.rule("[bold]Stage 6: ✨ 输出 (Silk)")
            output_path = write_silk(SILK_DIR, SITE_NAME, record, content)
            console.print(f"    输出文件: [bold]{output_path}[/]")
            if output_path.exists():
                file_size = len(output_path.read_text(encoding="utf-8"))
                console.print(f"    文件大小: {file_size} 字符")

    # ── Summary Report ──
    console.rule("[bold red]📊 测试总结")
    summary_table = Table(title="端到端测试结果", box=box.HEAVY)
    summary_table.add_column("阶段", style="cyan")
    summary_table.add_column("状态", style="green")
    summary_table.add_column("详情")

    summary_table.add_row("🍃 发现", "✅", f"从 sitemap 发现 {len(all_urls)} 个 URL")
    summary_table.add_row("📥 抓取", "✅" if records else "❌", f"成功抓取 {sum(1 for r in records if r.status == 'fetched')}/{len(records)} 个页面")
    summary_table.add_row("🔍 检测", "✅", f"检测到框架: {records[0].framework if records else 'N/A'}")
    summary_table.add_row("🧹 清洗", "✅" if any(r.status == 'fetched' for r in records) else "❌", "8个 Filter 管线执行完成")
    summary_table.add_row("✅ 质检", "✅", "质量门禁执行完成")
    summary_table.add_row("✨ 输出", "✅", f"输出目录: {SILK_DIR}")
    console.print(summary_table)
    console.print(f"\n输出文件列表:")
    for f in sorted(SILK_DIR.rglob("*.md")):
        console.print(f"  📄  {f.relative_to(SILK_DIR)}")


if __name__ == "__main__":
    asyncio.run(main())
