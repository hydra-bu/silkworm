"""CLI 入口 — 基于 typer。全流程 Feed → Spin → Silk 一站式接入。"""

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import typer
from rich.console import Console
from rich.table import Table

from silkworm import __version__
from silkworm.config import AppConfig

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="silkworm",
    help="Silkworm — 站点文档抓取工具. Feed it pages, get back silk.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo("silkworm v{0}".format(__version__))
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, help="显示版本号"
    ),
) -> None:
    pass


def _derive_site_name(url: str) -> str:
    """从 URL 提取域名作为顶层目录名（例如 docs.pydantic.dev）。"""
    return urlparse(url).hostname or "site"


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size = int(size / 1024)
    return f"{size:.1f}GB"


_COCOON_MANIFEST = "_manifest.json"


def _save_cocoon_manifest(cocoon: Path, records: list) -> None:
    import json
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(records),
        "pages": [
            {
                "url": r.url,
                "http_status": r.http_status,
                "content_hash": r.content_hash,
                "html_file": r.raw_html_path,
                "status": r.status,
            }
            for r in records
        ],
    }
    (cocoon / _COCOON_MANIFEST).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _load_cocoon_manifest(cocoon: Path) -> dict:
    import json
    mfp = cocoon / _COCOON_MANIFEST
    if not mfp.exists():
        return {"pages": []}
    return json.loads(mfp.read_text(encoding="utf-8"))


@app.command()
def feed(
    url: str = typer.Argument(..., help="目标站点 URL"),
    prompt: str = typer.Option(
        "",
        "--prompt",
        "-p",
        help="导航过滤：空=全量，指定章节名=仅抓取该章节，如 'tutorial'",
    ),
    site: str = typer.Option(
        "",
        "--site",
        "-s",
        help="站点名称（输出目录名），默认从 URL 推导",
    ),
    config: Path = typer.Option(
        Path("silkworm.yaml"), "--config", "-c", help="配置文件路径"
    ),
    cocoon: Path = typer.Option(
        Path("cocoon"), "--cocoon", help="原始 HTML 存储目录"
    ),
    discover_only: bool = typer.Option(
        False, "--discover-only", "-d", help="仅发现 URL，不执行抓取"
    ),
    exclude: str = typer.Option(
        "",
        "--exclude",
        "-e",
        help="排除的导航章节标题（逗号分隔），如 'Contribute,Reference'",
    ),
    skip_pipeline: bool = typer.Option(
        False, "--skip-pipeline", help="跳过清洗/质检，仅保存原始 HTML"
    ),
) -> None:
    """Feed — 发现 → 抓取 → 清洗 → 输出，全流程一站式。"""
    cfg = AppConfig.from_yaml(config)
    site_name = site or _derive_site_name(url)
    exclude_list = [e.strip() for e in exclude.split(",") if e.strip()] if exclude else []
    _run_feed(url, prompt, site_name, cfg, cocoon, discover_only, skip_pipeline, exclude_list)


def _run_feed(
    url: str,
    prompt: str,
    site_name: str,
    cfg: AppConfig,
    cocoon: Path,
    discover_only: bool,
    skip_pipeline: bool,
    exclude_list: list[str] | None = None,
) -> None:
    import asyncio

    silk_dir = cfg.global_config.silk_dir

    console.print(f"[bold]🌐 目标:[/]  {url}")
    console.print(f"[bold]🏷  站点:[/]  {site_name}")
    if prompt:
        console.print(f"[bold]🔍 过滤:[/]  '{prompt}'")
    console.print(f"[bold]📁 Cocoon:[/] {cocoon.resolve()}")
    console.print(f"[bold]📁 Silk:[/]   {silk_dir.resolve()}")

    if exclude_list:
        console.print(f"[bold]🚫 排除:[/]  {', '.join(exclude_list)}")
    console.print("\n[bold cyan]▸ 阶段 1/3: 发现 URL[/]")
    urls, pri = asyncio.run(_do_discover(url, prompt, exclude_list, cfg))
    if not urls:
        console.print("[red]❌ 未发现任何 URL，终止[/]")
        raise typer.Exit(1)
    if pri:
        console.print(f"  → 发现 [bold]{len(urls)}[/] 个 URL"
                      + f"（核心 [green]{pri.get('core', 0)}[/] 个 | 次要 [yellow]{pri.get('secondary', 0)}[/] 个）"
                      + (f"  过滤: '{prompt}'" if prompt else ""))
    else:
        console.print(f"  → 发现 [bold]{len(urls)}[/] 个 URL"
                      + (f"（过滤: '{prompt}'）" if prompt else ""))

    if discover_only:
        if pri:
            console.print(f"\n[bold]核心页面[/]（{pri.get('core', 0)} 个）:")
            core_shown = 0
            for u in urls:
                if core_shown >= 25:
                    console.print(f"    ...（共 {pri.get('core', 0)} 个）")
                    break
                # We don't have per-URL priority info here
                console.print(f"    {u}")
                core_shown += 1
            console.print(f"\n[bold]次要页面[/]（{pri.get('secondary', 0)} 个）:")
            sec_shown = 0
            for u in urls[pri.get('core', 0):][:25]:
                console.print(f"    {u}")
                sec_shown += 1
            remaining = len(urls) - pri.get('core', 0) - sec_shown
            if remaining > 0:
                console.print(f"    ...（共 {pri.get('secondary', 0)} 个）")
        else:
            for u in urls[:50]:
                console.print(f"    {u}")
            if len(urls) > 50:
                console.print(f"    ...（共 {len(urls)} 个）")
        return

    console.print(f"\n[bold cyan]▸ 阶段 2/3: 抓取页面[/]")
    records = asyncio.run(_do_fetch(urls, cocoon))
    ok = [r for r in records if r.http_status == 200]
    console.print(f"  → 成功 [green]{len(ok)}[/]，失败 [red]{len(records) - len(ok)}[/]")

    _save_cocoon_manifest(cocoon, records)

    if skip_pipeline or not ok:
        if skip_pipeline:
            console.print("\n[yellow]⏭  跳过清洗阶段（--skip-pipeline）[/]")
        return

    console.print(f"\n[bold cyan]▸ 阶段 3/3: 清洗 → 质检 → 输出 Markdown[/]")
    results = _do_process(ok, cocoon, cfg, site_name)
    _print_summary(site_name, results, silk_dir)


@app.command()
def spin(
    site: str = typer.Argument(..., help="站点名称（输出目录名）"),
    cocoon: Path = typer.Option(
        Path("cocoon"), "--cocoon", help="原始 HTML 存储目录"
    ),
    config: Path = typer.Option(
        Path("silkworm.yaml"), "--config", "-c", help="配置文件路径"
    ),
) -> None:
    """Spin — 清洗 cocoon 中已抓取的原始 HTML，输出 Markdown。"""
    cfg = AppConfig.from_yaml(config)
    silk_dir = cfg.global_config.silk_dir

    console.print(f"[bold]🧵 站点:[/]    {site}")
    console.print(f"[bold]📁 Cocoon:[/]  {cocoon.resolve()}")
    console.print(f"[bold]📁 Silk:[/]    {silk_dir.resolve()}")

    manifest = _load_cocoon_manifest(cocoon)
    pages = manifest.get("pages", [])

    if not pages:
        html_files = sorted(cocoon.rglob("*.html"))
        if not html_files:
            console.print("[red]❌ Cocoon 为空，无页面可清洗。请先运行 silkworm feed[/]")
            raise typer.Exit(1)
        console.print(f"[yellow]⚠️  未找到 _manifest.json，扫描到 {len(html_files)} 个 HTML 文件[/]")
        from silkworm.models import PageRecord
        records = [
            PageRecord(url=f"cocoon:{f.stem}", http_status=200, raw_html_path=f.name, status="fetched")
            for f in html_files
        ]
    else:
        from silkworm.models import PageRecord
        records = [
            PageRecord(
                url=p["url"],
                http_status=p.get("http_status", 0),
                content_hash=p.get("content_hash", ""),
                raw_html_path=p.get("html_file", ""),
                status=p.get("status", "fetched"),
            )
            for p in pages
        ]
        console.print(f"  → 清单中发现 [bold]{len(records)}[/] 个页面")

    ok = [r for r in records if r.http_status == 200]
    if not ok:
        console.print("[red]❌ 无成功抓取的页面可处理[/]")
        raise typer.Exit(1)

    console.print(f"\n[bold cyan]▸ 清洗 → 质检 → 输出 Markdown[/]")
    results = _do_process(ok, cocoon, cfg, site)
    _print_summary(site, results, silk_dir)


@app.command()
def cocoon(
    action: str = typer.Argument(
        "list", help="操作: list / status / clean"
    ),
    cocoon: Path = typer.Option(
        Path("cocoon"), "--cocoon", help="原始 HTML 存储目录"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="clean 时跳过确认"
    ),
) -> None:
    """Cocoon — 管理原始 HTML 存储区。"""
    actions = {"list": _cocoon_list, "status": _cocoon_status, "clean": _cocoon_clean}
    fn = actions.get(action)
    if fn:
        fn(cocoon, force)
    else:
        console.print(f"[red]未知操作: {action}。支持: list / status / clean[/]")


def _cocoon_list(cocoon: Path, force: bool = False) -> None:
    if not cocoon.exists():
        console.print("[yellow]Cocoon 目录不存在[/]")
        return

    html_files = sorted(cocoon.rglob("*.html"))
    manifest = _load_cocoon_manifest(cocoon)

    console.print(f"[bold]📁 {cocoon.resolve()}[/]")
    console.print(f"  HTML 文件: [bold]{len(html_files)}[/]")

    pages = manifest.get("pages", [])
    if pages:
        console.print(f"  清单记录: [bold]{len(pages)}[/]")
        table = Table(show_header=True, header_style="bold")
        table.add_column("文件", style="dim")
        table.add_column("URL", overflow="fold")
        table.add_column("状态")
        for p in pages[:30]:
            table.add_row(
                p.get("html_file", ""),
                p.get("url", ""),
                "✅" if p.get("http_status") == 200 else "❌",
            )
        if len(pages) > 30:
            console.print(f"  ...（前 30 条，共 {len(pages)} 条）")
        console.print(table)
    elif html_files:
        table = Table(show_header=True, header_style="bold")
        table.add_column("文件", style="dim")
        table.add_column("大小")
        for f in html_files:
            table.add_row(f.name, _fmt_size(f.stat().st_size))
        console.print(table)


def _cocoon_status(cocoon: Path, force: bool = False) -> None:
    if not cocoon.exists():
        console.print("[yellow]Cocoon 目录不存在[/]")
        return

    html_files = list(cocoon.rglob("*.html"))
    total_size = sum(f.stat().st_size for f in html_files)
    manifest = _load_cocoon_manifest(cocoon)

    table = Table(show_header=True, header_style="bold")
    table.add_column("指标")
    table.add_column("数值")
    table.add_row("HTML 文件数", str(len(html_files)))
    table.add_row("总大小", _fmt_size(total_size))
    table.add_row("清单记录数", str(len(manifest.get("pages", []))))
    if manifest.get("generated_at"):
        table.add_row("生成时间", manifest["generated_at"])
    console.print(table)


def _cocoon_clean(cocoon: Path, force: bool) -> None:
    if not cocoon.exists():
        console.print("[yellow]Cocoon 目录不存在[/]")
        return

    html_files = list(cocoon.rglob("*.html"))
    mfp = cocoon / _COCOON_MANIFEST
    if not html_files and not mfp.exists():
        console.print("[yellow]Cocoon 已经为空[/]")
        return

    if not force:
        console.print(f"[yellow]⚠️  将删除 {len(html_files)} 个文件。使用 --force 确认。[/]")
        return

    for f in html_files:
        f.unlink()
    if mfp.exists():
        mfp.unlink()
    try:
        if cocoon.exists() and not list(cocoon.iterdir()):
            cocoon.rmdir()
    except OSError:
        pass

    console.print(f"[green]✅ Cocoon 已清空（{len(html_files)} 个文件 + 清单）[/]")


async def _do_discover(
    url: str, prompt: str, exclude_list: list[str] | None, cfg: AppConfig
) -> tuple[list[str], dict | None]:
    from silkworm.feed.navigator import discover_navigation
    prompt_val = prompt if prompt.strip() else None
    return await discover_navigation(
        url, prompt=prompt_val, exclude=exclude_list, llm_config=cfg.global_config.llm
    )


async def _do_fetch(urls: list[str], cocoon: Path) -> list:
    import httpx
    from silkworm.feed.fetcher import fetch_page

    user_agent = "Silkworm/0.1.0 (+https://github.com/silkworm)"

    records = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        for i, url in enumerate(urls, 1):
            record = await fetch_page(client, url, cocoon, user_agent)
            if record.http_status == 200:
                console.print(f"  [{i}/{len(urls)}] ✅ {url}  （{record.content_hash[:12]}…）")
            else:
                detail = record.quality_report.get("error") if record.quality_report else ""
                console.print(f"  [{i}/{len(urls)}] ❌ {url}  （HTTP {record.http_status}{' - ' + detail if detail else ''}）")
            records.append(record)
    return records


def _do_process(
    records: list, cocoon: Path, cfg: AppConfig, site_name: str
) -> list[tuple]:
    from silkworm.pipeline import PipelineOrchestrator
    from silkworm.silk.output import write_silk

    orchestrator = PipelineOrchestrator(
        max_retries=cfg.global_config.max_retries,
        quality_min_length=cfg.global_config.quality_min_length,
        quality_threshold=cfg.global_config.quality_score_threshold,
        llm_config=cfg.global_config.llm,
    )

    results = []
    for i, record in enumerate(records, 1):
        html_path = cocoon / record.raw_html_path
        if not html_path.exists():
            console.print(f"  [{i}/{len(records)}] ⚠️  {record.url}  （原始 HTML 文件丢失）")
            results.append((record, None, None))
            continue

        raw_html = html_path.read_text(encoding="utf-8", errors="replace")
        try:
            result = orchestrator.run(raw_html, record)
        except Exception as e:
            console.print(f"  [{i}/{len(records)}] ❌ {record.url}  （管线异常: {e}）")
            results.append((record, None, None))
            continue

        output_path = write_silk(
            cfg.global_config.silk_dir, site_name, result.record, result.content
        )

        icon = "✅" if result.final_report.passed else "🟡"
        tag = f"  分数: {result.final_report.total_score:.2f}"
        if result.retry_count > 0:
            tag += f"  重试: {result.retry_count} 次"
        console.print(f"  [{i}/{len(records)}] {icon} {record.url}  （{tag}）")
        results.append((result.record, result.content, output_path))

    return results


def _print_summary(
    site_name: str, results: list[tuple], silk_dir: Path
) -> None:
    auto_pass = sum(1 for r in results if r[1] is not None
                    and (not hasattr(r[0], 'status') or r[0].status != "manual_review"))
    manual_review = sum(1 for r in results if r[1] is not None
                        and hasattr(r[0], 'status') and r[0].status == "manual_review")
    failed = len(results) - auto_pass - manual_review

    console.print("\n[bold]═══  汇总报告  ═══[/]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("状态")
    table.add_column("数量")
    table.add_column("说明")
    table.add_row("[green]✅ 通过[/]", str(auto_pass), "清洗质检全通过")
    table.add_row("[yellow]🟡 待人工[/]", str(manual_review), "质检未达标，标记 manual_review")
    table.add_row("[red]❌ 失败[/]", str(failed), "抓取或管线异常")
    table.add_row("[bold]合计[/]", str(len(results)), "")
    console.print(table)

    out_count = auto_pass + manual_review
    if out_count > 0:
        console.print(f"\n[bold]📄 输出:[/] {silk_dir.resolve()}")
        md_files = sorted(silk_dir.glob("**/*.md"))
        for f in md_files[:8]:
            console.print(f"    📄 {f.relative_to(silk_dir)}  ({_fmt_size(f.stat().st_size)})")
        if len(md_files) > 8:
            console.print(f"    ...（共 {len(md_files)} 个文件）")
