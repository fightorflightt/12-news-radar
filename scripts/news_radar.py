#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import ssl
import subprocess
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "settings.json"
SOURCES_PATH = ROOT / "config" / "sources.json"


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = dict(attrs)
        self._href = attrs_dict.get("href")
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        title = " ".join("".join(self._text_parts).split())
        if title:
            self.links.append((title, self._href))
        self._href = None
        self._text_parts = []


@dataclass(frozen=True)
class Item:
    title: str
    source: str
    url: str
    summary: str
    meta: str = ""


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def now_in_timezone(settings: dict[str, Any]) -> datetime:
    return datetime.now(ZoneInfo(settings.get("timezone", "Asia/Shanghai")))


def run_text(command: list[str], timeout: int = 30) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout


def collect_youtube(sources: dict[str, Any], limit_per_channel: int = 1) -> tuple[list[Item], list[str]]:
    items: list[Item] = []
    errors: list[str] = []
    if not shutil.which("yt-dlp"):
        return items, ["未找到 yt-dlp，YouTube 最新视频暂时无法抓取。"]

    for channel in sources.get("youtube", []):
        command = [
            "yt-dlp",
            "--no-check-certificates",
            "--flat-playlist",
            "--dump-json",
            "--playlist-end",
            str(limit_per_channel),
            channel["url"],
        ]
        try:
            output = run_text(command, timeout=45)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{channel['name']}：{exc}")
            continue

        for line in output.splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            title = data.get("title") or "未命名视频"
            url = data.get("url") or data.get("webpage_url") or ""
            if url and not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={url}"
            view_count = data.get("view_count")
            meta = f"{channel.get('role', '')}"
            if view_count:
                meta = f"{meta}；播放量约 {view_count}"
            items.append(
                Item(
                    title=title,
                    source=channel["name"],
                    url=url,
                    summary=f"最新视频：{title}。适合用于快速观察 {channel['name']} 最近关注的 AI 变现、内容或创业方向。",
                    meta=meta.strip("；"),
                )
            )
    return items, errors


def collect_github(sources: dict[str, Any]) -> tuple[list[Item], list[str]]:
    config = sources.get("github", {})
    topics = config.get("topics", ["ai"])
    limit = int(config.get("limit", 20))
    query = " ".join(f"topic:{topic}" for topic in topics)
    if not shutil.which("gh"):
        return [], ["未找到 GitHub CLI，GitHub 热门暂时无法抓取。"]

    command = [
        "gh",
        "search",
        "repos",
        query,
        "--sort",
        "stars",
        "--limit",
        str(limit),
        "--json",
        "fullName,description,url,stargazersCount,updatedAt",
    ]
    try:
        output = run_text(command, timeout=45)
        repos = json.loads(output)
    except Exception as exc:  # noqa: BLE001
        return [], [f"GitHub 热门：{exc}"]

    items = []
    for repo in repos:
        description = repo.get("description") or "暂无简介"
        stars = repo.get("stargazersCount")
        updated = repo.get("updatedAt", "")[:10]
        items.append(
            Item(
                title=repo.get("fullName", "unknown/repo"),
                source="GitHub",
                url=repo.get("url", ""),
                summary=description,
                meta=f"Stars: {stars}；更新: {updated}",
            )
        )
    return items, []


def collect_ai_builders(sources: dict[str, Any]) -> list[Item]:
    source = sources.get("ai_builders", {})
    url = source.get("url", "https://github.com/zarazhangrui/follow-builders")
    return [
        Item(
            title="follow-builders",
            source="AI Builder",
            url=url,
            summary="第一版先保留 follow-builders 入口。后续可接入它的 feed 或运行它的脚本，把 Builder 动态合并进日报。",
            meta="待接入 feed",
        )
    ]


def fetch_links_from_source(source: dict[str, Any], max_links: int = 8) -> tuple[list[Item], str | None]:
    url = source["url"]
    keywords = source.get("keywords", [])
    try:
        request = Request(url, headers={"User-Agent": "12-news-radar/1.0"})
        context = ssl._create_unverified_context()
        with urlopen(request, timeout=12, context=context) as response:
            raw = response.read()
        text = raw.decode("utf-8", errors="replace")
    except (OSError, URLError) as exc:
        return [], f"{source['name']}：{exc}"

    parser = LinkParser()
    parser.feed(text)
    found: list[Item] = []
    seen: set[str] = set()
    for title, href in parser.links:
        if not title or not href:
            continue
        if keywords and not any(keyword in title for keyword in keywords):
            continue
        absolute = urljoin(url, href)
        key = f"{title}|{absolute}"
        if key in seen:
            continue
        seen.add(key)
        found.append(
            Item(
                title=title,
                source=source["name"],
                url=absolute,
                summary=f"在 {source['name']} 发现与关注关键词相关的入口，建议人工核对原文。",
                meta="政策/产业/岗位线索",
            )
        )
        if len(found) >= max_links:
            break
    return found, None


def collect_policy_jobs(sources: dict[str, Any]) -> tuple[list[Item], list[str]]:
    items: list[Item] = []
    errors: list[str] = []
    for source in sources.get("policy_job_sources", []):
        found, error = fetch_links_from_source(source)
        if error:
            errors.append(error)
        items.extend(found)

    if not items:
        for source in sources.get("policy_job_sources", []):
            items.append(
                Item(
                    title=f"{source['name']}：今日未自动匹配到关键词链接",
                    source=source["name"],
                    url=source["url"],
                    summary="已保留官网入口。建议后续为该来源配置 RSS、栏目页或站内搜索地址，提高命中率。",
                    meta="来源入口",
                )
            )
    return items, errors


def should_include_github(date: datetime, task: str) -> bool:
    if task == "weekly-github":
        return True
    if task != "daily":
        return False
    return date.weekday() in (0, 4)


def collect(task: str, date: datetime) -> dict[str, Any]:
    sources = load_json(SOURCES_PATH)
    sections: dict[str, list[Item]] = {}
    errors: list[str] = []

    if task in ("daily", "ai-money"):
        youtube, youtube_errors = collect_youtube(sources)
        sections["AI 搞钱"] = youtube
        errors.extend(youtube_errors)

    if should_include_github(date, task):
        github, github_errors = collect_github(sources)
        sections["GitHub 热门 Top20"] = github
        errors.extend(github_errors)

    if task in ("daily", "ai-builders"):
        sections["AI Builder"] = collect_ai_builders(sources)

    if task in ("daily", "policy-jobs"):
        policy_jobs, policy_errors = collect_policy_jobs(sources)
        sections["政策、产业与岗位机会雷达"] = policy_jobs
        errors.extend(policy_errors)

    if task == "daily-review":
        sections["每日成长复盘"] = build_daily_review(date)

    if task == "weekly-plan":
        sections["每周行动清单"] = build_weekly_plan(date)

    if task == "weekly-opportunity-review":
        sections["每周机会复盘"] = build_weekly_opportunity_review(date)

    return {
        "date": date.date().isoformat(),
        "task": task,
        "sections": sections,
        "errors": errors,
    }


def read_recent_markdown(obsidian_dir: Path, days: int = 7) -> str:
    if not obsidian_dir.exists():
        return ""
    cutoff = datetime.now().timestamp() - days * 86400
    chunks: list[str] = []
    for path in sorted(obsidian_dir.glob("*.md")):
        if path.stat().st_mtime < cutoff:
            continue
        chunks.append(f"# {path.name}\n\n{path.read_text(encoding='utf-8', errors='replace')[:4000]}")
    return "\n\n".join(chunks)


def build_daily_review(date: datetime) -> list[Item]:
    settings = load_json(SETTINGS_PATH)
    obsidian_dir = Path(settings["obsidian_dir"])
    text = read_recent_markdown(obsidian_dir, days=1)
    if not text:
        return [
            Item(
                title="今天先写 1 条复盘",
                source="本地文档",
                url="",
                summary="今日暂未读取到可复盘文档。建议只写 1 条：今天最值得记住的信息是什么？",
                meta="不超过 3 条",
            )
        ]
    lines = [line.strip("#- 0123456789.[]") for line in text.splitlines() if line.strip()]
    candidates = [line for line in lines if 8 <= len(line) <= 80][:3]
    return [
        Item(
            title=f"复盘提醒 {index}",
            source="本地文档",
            url="",
            summary=candidate,
            meta="自动从当天文档提取",
        )
        for index, candidate in enumerate(candidates or ["把今天的信息整理成 1 个明天能执行的动作。"], start=1)
    ][:3]


def build_weekly_plan(date: datetime) -> list[Item]:
    settings = load_json(SETTINGS_PATH)
    obsidian_dir = Path(settings["obsidian_dir"])
    text = read_recent_markdown(obsidian_dir, days=7)
    if not text:
        actions = ["本周先跑通资讯系统，再逐步增加来源。"]
    else:
        titles = re.findall(r"^#+\s+(.+)$", text, flags=re.M)
        actions = [f"围绕“{title[:40]}”整理一个可执行动作。" for title in titles[:5]]
    return [
        Item(title=f"行动 {i}", source="上周内容", url="", summary=action, meta="本周计划")
        for i, action in enumerate(actions[:5], start=1)
    ]


def build_weekly_opportunity_review(date: datetime) -> list[Item]:
    settings = load_json(SETTINGS_PATH)
    obsidian_dir = Path(settings["obsidian_dir"])
    text = read_recent_markdown(obsidian_dir, days=7)
    keywords = ("政策", "岗位", "招聘", "产业", "国企", "事业单位", "公务员")
    lines = [line.strip("- ") for line in text.splitlines() if any(key in line for key in keywords)]
    if not lines:
        lines = ["本周暂未提取到明确机会，建议检查政策/岗位来源是否需要补充栏目页。"]
    return [
        Item(title=f"机会复盘 {i}", source="本周机会雷达", url="", summary=line[:120], meta="周五复盘")
        for i, line in enumerate(lines[:5], start=1)
    ]


def item_to_markdown(item: Item) -> str:
    parts = [f"- **{item.title}**"]
    if item.summary:
        parts.append(f"  - 摘要：{item.summary}")
    if item.meta:
        parts.append(f"  - 信息：{item.meta}")
    if item.url:
        parts.append(f"  - 来源：{item.url}")
    return "\n".join(parts)


def render_markdown(report: dict[str, Any]) -> str:
    date = report["date"]
    task = report["task"]
    lines = [
        "---",
        f"title: {date} 资讯雷达",
        f"date: {date}",
        f"task: {task}",
        "tags:",
        "  - 资讯雷达",
        "  - 自动化",
        "---",
        "",
        f"# {date} 资讯雷达",
        "",
        "## 今日摘要",
    ]
    section_count = sum(len(items) for items in report["sections"].values())
    lines.append(f"- 本次生成 {len(report['sections'])} 个板块，共 {section_count} 条信息。")
    if report["errors"]:
        lines.append(f"- 有 {len(report['errors'])} 个来源抓取失败，已记录在文末。")
    else:
        lines.append("- 所有已配置来源均完成处理。")

    for section, items in report["sections"].items():
        lines.extend(["", f"## {section}", ""])
        if not items:
            lines.append("- 今日暂无内容。")
            continue
        for item in items:
            lines.append(item_to_markdown(item))

    if report["errors"]:
        lines.extend(["", "## 抓取问题", ""])
        for error in report["errors"]:
            lines.append(f"- {error}")
    lines.append("")
    return "\n".join(lines)


def slugify_anchor(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", text).strip("-").lower()


def render_html(report: dict[str, Any], markdown_name: str) -> str:
    date = report["date"]
    sections = report["sections"]
    nav = "\n".join(
        f'<a href="#{slugify_anchor(section)}">{html.escape(section)}</a>'
        for section in sections
    )
    body_sections: list[str] = []
    for section, items in sections.items():
        cards = []
        for item in items:
            link = (
                f'<a class="source-link" href="{html.escape(item.url)}" target="_blank" rel="noreferrer">来源</a>'
                if item.url
                else ""
            )
            cards.append(
                "<article class=\"item\">"
                f"<h3>{html.escape(item.title)}</h3>"
                f"<p>{html.escape(item.summary)}</p>"
                f"<div class=\"meta\"><span>{html.escape(item.source)}</span><span>{html.escape(item.meta)}</span>{link}</div>"
                "</article>"
            )
        if not cards:
            cards.append("<p class=\"empty\">今日暂无内容。</p>")
        body_sections.append(
            f'<section id="{slugify_anchor(section)}"><h2>{html.escape(section)}</h2>{"".join(cards)}</section>'
        )

    errors = ""
    if report["errors"]:
        errors = "<section id=\"errors\"><h2>抓取问题</h2><ul>" + "".join(
            f"<li>{html.escape(error)}</li>" for error in report["errors"]
        ) + "</ul></section>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(date)} 资讯雷达</title>
  <link rel="stylesheet" href="./assets/style.css">
</head>
<body>
  <aside class="sidebar">
    <div class="brand">12 资讯雷达</div>
    <div class="date">{html.escape(date)}</div>
    <nav>{nav}<a href="./index.html">返回首页</a></nav>
  </aside>
  <main>
    <header class="page-header">
      <p>资讯摘要</p>
      <h1>{html.escape(date)} 资讯雷达</h1>
      <div class="summary">
        <span>{len(sections)} 个板块</span>
        <span>{sum(len(items) for items in sections.values())} 条信息</span>
        <a href="../obsidian/{html.escape(markdown_name)}">Markdown</a>
      </div>
    </header>
    {''.join(body_sections)}
    {errors}
  </main>
</body>
</html>
"""


def ensure_style(site_dir: Path) -> None:
    assets = site_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    style = assets / "style.css"
    if style.exists():
        return
    style.write_text(
        """
:root {
  color-scheme: light;
  --bg: #f7f5ef;
  --panel: #ffffff;
  --ink: #202124;
  --muted: #6b6f76;
  --line: #dedbd2;
  --accent: #176b5c;
  --accent-2: #9b3d2e;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  padding: 28px 22px;
  border-right: 1px solid var(--line);
  background: #f0eee7;
}
.brand { font-size: 18px; font-weight: 700; }
.date { margin-top: 8px; color: var(--muted); font-size: 14px; }
nav { display: grid; gap: 10px; margin-top: 28px; }
nav a {
  color: var(--ink);
  text-decoration: none;
  padding: 8px 0;
  border-bottom: 1px solid rgba(32, 33, 36, .1);
}
nav a:hover { color: var(--accent); }
main {
  max-width: 980px;
  width: 100%;
  padding: 42px 40px 80px;
}
.page-header {
  border-bottom: 1px solid var(--line);
  padding-bottom: 24px;
  margin-bottom: 28px;
}
.page-header p {
  margin: 0 0 8px;
  color: var(--accent-2);
  font-weight: 700;
}
h1 {
  margin: 0;
  font-size: 38px;
  line-height: 1.12;
  letter-spacing: 0;
}
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
  color: var(--muted);
}
.summary span, .summary a {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 6px 10px;
  background: rgba(255, 255, 255, .5);
  color: inherit;
  text-decoration: none;
}
section { margin-top: 34px; }
h2 {
  font-size: 24px;
  margin: 0 0 14px;
  letter-spacing: 0;
}
.item {
  padding: 18px 0;
  border-top: 1px solid var(--line);
}
.item h3 {
  margin: 0 0 8px;
  font-size: 18px;
  line-height: 1.4;
  letter-spacing: 0;
}
.item p {
  margin: 0;
  color: #35383d;
  line-height: 1.72;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 10px;
  color: var(--muted);
  font-size: 13px;
}
.source-link { color: var(--accent); }
.empty { color: var(--muted); }
@media (max-width: 760px) {
  body { display: block; }
  .sidebar {
    position: relative;
    height: auto;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }
  nav {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 4px 14px;
  }
  main { padding: 28px 20px 56px; }
  h1 { font-size: 30px; }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )


def render_index(site_dir: Path) -> None:
    pages = sorted(
        [path for path in site_dir.glob("*.html") if path.name != "index.html"],
        reverse=True,
    )
    links = "\n".join(
        f'<a class="index-link" href="./{html.escape(path.name)}"><span>{html.escape(path.stem)}</span><small>打开日报</small></a>'
        for path in pages
    )
    (site_dir / "index.html").write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>12 资讯雷达</title>
  <link rel="stylesheet" href="./assets/style.css">
  <style>
    body {{ display: block; }}
    main {{ margin: 0 auto; }}
    .index-link {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 0;
      border-top: 1px solid var(--line);
      color: var(--ink);
      text-decoration: none;
    }}
    .index-link small {{ color: var(--muted); }}
  </style>
</head>
<body>
  <main>
    <header class="page-header">
      <p>GitHub Pages</p>
      <h1>12 资讯雷达</h1>
      <div class="summary"><span>{len(pages)} 个页面</span></div>
    </header>
    <section>
      <h2>日报归档</h2>
      {links or '<p class="empty">还没有生成日报。</p>'}
    </section>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )


def write_outputs(report: dict[str, Any]) -> tuple[Path, Path]:
    settings = load_json(SETTINGS_PATH)
    date = report["date"]
    task = report["task"]
    suffix = "" if task == "daily" else f"-{task}"
    markdown_name = f"{date}{suffix}.md"
    html_name = f"{date}{suffix}.html"

    obsidian_dir = Path(settings["obsidian_dir"])
    obsidian_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = obsidian_dir / markdown_name
    markdown = render_markdown(report)
    markdown_path.write_text(markdown, encoding="utf-8")

    site_dir = ROOT / settings.get("site_dir", "site")
    site_dir.mkdir(parents=True, exist_ok=True)
    ensure_style(site_dir)
    html_path = site_dir / html_name
    html_path.write_text(render_html(report, markdown_name), encoding="utf-8")

    obsidian_copy_dir = site_dir / "obsidian"
    obsidian_copy_dir.mkdir(exist_ok=True)
    (obsidian_copy_dir / markdown_name).write_text(markdown, encoding="utf-8")
    render_index(site_dir)
    return markdown_path, html_path


def notify(title: str, body: str) -> None:
    settings = load_json(SETTINGS_PATH)
    notification = settings.get("notification", {})
    command = notification.get("hermes_command") or os.environ.get("HERMES_COMMAND", "")
    target = notification.get("hermes_target") or os.environ.get("HERMES_TARGET", "")
    if not command:
        print("Hermes notification skipped: HERMES_COMMAND is not configured.")
        return
    env = os.environ.copy()
    if target:
        env["HERMES_TARGET"] = target
    subprocess.run(
        command.format(title=title, body=body, target=target),
        shell=True,
        cwd=ROOT,
        env=env,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate personal news radar markdown and HTML.")
    parser.add_argument(
        "task",
        choices=[
            "daily",
            "ai-money",
            "ai-builders",
            "policy-jobs",
            "daily-review",
            "weekly-plan",
            "weekly-github",
            "weekly-opportunity-review",
        ],
        nargs="?",
        default="daily",
    )
    parser.add_argument("--date", help="Date in YYYY-MM-DD. Defaults to today in Asia/Shanghai.")
    parser.add_argument("--notify", action="store_true", help="Send completion notification through Hermes.")
    args = parser.parse_args()

    settings = load_json(SETTINGS_PATH)
    date = (
        datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=ZoneInfo(settings.get("timezone", "Asia/Shanghai")))
        if args.date
        else now_in_timezone(settings)
    )
    report = collect(args.task, date)
    markdown_path, html_path = write_outputs(report)
    print(f"Markdown: {markdown_path}")
    print(f"HTML: {html_path}")
    if args.notify:
        notify(
            f"{report['date']} 资讯雷达已生成",
            f"Obsidian: {markdown_path}\nHTML: {html_path}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
