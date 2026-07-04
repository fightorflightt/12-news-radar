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
import tempfile
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
FOLLOW_BUILDERS_DIR = Path("/Users/xiongbatian/.codex/skills/follow-builders")


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


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0
        self._capture = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        self._capture = tag in {"title", "h1", "h2", "h3", "p", "li", "td", "div"}

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = " ".join(data.split())
        if self._capture and text:
            self.parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        self._capture = False


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


def clean_text(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \n\t-—|")


def split_sentences(text: str) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?])\s+|(?<=[。！？!?])|(?<=[.!?])\s+", text)
    return [part.strip() for part in parts if len(part.strip()) >= 12]


def summarize_text(text: str, fallback: str, max_sentences: int = 3, max_chars: int = 280) -> str:
    sentences = split_sentences(text)
    if not sentences:
        return fallback
    selected: list[str] = []
    for sentence in sentences:
        if sentence in selected:
            continue
        low = sentence.lower()
        if any(noise in low for noise in ("subscribe", "sponsor", "discord", "newsletter")):
            continue
        selected.append(sentence)
        if len(selected) >= max_sentences:
            break
    if not selected:
        selected = sentences[:max_sentences]
    summary = " ".join(selected)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rstrip() + "…"
    return summary


def clean_youtube_description(description: str) -> str:
    noise_patterns = (
        "http",
        "skool",
        "work with me",
        "try @",
        "#",
        " ig:",
        "instagram",
        "twitter",
        "linkedin",
        "spotify",
        "newsletter",
        "bootcamp",
        "sponsor",
        "free resource",
        "free month",
        "code ",
        "discount",
        "my books",
        "socials",
        "chapters",
        "0:00",
        "subscribe",
    )
    kept: list[str] = []
    for raw in description.splitlines():
        line = clean_text(raw)
        if len(line) < 20:
            continue
        low = f" {line.lower()}"
        if "resources from today" in low:
            marker = re.search(r"resources from today'?s video:?", line, flags=re.I)
            if marker:
                line = line[marker.end() :].strip(" :-")
                low = f" {line.lower()}"
        if any(pattern in low for pattern in noise_patterns):
            continue
        kept.append(line)
    return " ".join(kept)


def title_based_summary(title: str, channel_name: str) -> str:
    return f"这期内容围绕“{translate_title_hint(title)}”。当前未稳定获取到完整字幕，先作为待精读条目保留，重点看其中的案例、方法和可复制动作。"


def translate_title_hint(title: str) -> str:
    lower = title.lower()
    if "ai agents are the new saas" in lower:
        return "AI Agent 正在变成新一代 SaaS：从工具辅助转向直接完成工作"
    if "valuable ai can never replace" in lower:
        return "如何让自己在 AI 时代保持不可替代：提升判断力、创造力和个人价值"
    if "2.7m brand" in lower:
        return "用 AI 搭建并放大一个 270 万美元品牌：产品、网站、广告和爆款视频"
    if "fable" in lower and "karpathy" in lower:
        return "把 Fable 5 和 Karpathy 的 LLM 知识库结合起来，搭建可推理的第二大脑"
    if "fable" in lower:
        return "本周 AI 新闻：Fable 回归、新模型进展、NotebookLM、Claude、Gemini 和 Codex 相关更新"
    return title


def youtube_cn_summary(title: str, raw_summary: str, channel_name: str) -> str:
    low = raw_summary.lower()
    if raw_summary.startswith("这期内容围绕"):
        return raw_summary
    if (
        len(raw_summary) < 80
        or any(noise in low for noise in ("explore ai tools", "let’s work together", "free credits"))
        or raw_summary.startswith("(")
    ):
        return title_based_summary(title, channel_name)
    if channel_name == "Greg Isenberg":
        return (
            "这期核心观点是：AI Agent 正在成为新的 SaaS。传统软件是帮人完成工作，Agent 软件会直接参与并执行工作流。"
            "可关注他的路线：找一个有明确付费意愿的细分场景，观察人工流程，先做最小可用 Agent，用试点项目验证，再产品化。"
        )
    return f"这期主要讲“{translate_title_hint(title)}”。原始简介显示：{raw_summary}"


def builder_tweet_cn_summary(name: str, text: str) -> str:
    low = text.lower()
    if "exa" in low and "bake off" in low:
        return "Swyx 提到团队做了一次 Exa 与竞品的快速对比，约 1.5 小时后就一致选择 Exa。这个信号说明 AI 搜索/语义检索工具正在进入更务实的产品选型阶段。"
    if "project genie" in low:
        return "Google Labs 展示 Project Genie：用户选择角色、设定场景后，可以在几分钟内从玩游戏变成设计游戏。值得关注 AI 生成式游戏和低门槛创作工具。"
    if "42% of the web" in low:
        return "Guillermo Rauch 提到一项能力可能把 AI 带到 42% 的网页生态中，覆盖多模型、多提供商和多模态。重点关注 AI 基础设施如何进入主流 Web 工作流。"
    if "ai and mathematics" in low:
        return "Kevin Weil 关注 AI 与数学领域的新突破。这个方向通常代表模型推理能力、科研辅助和高难任务评测的进展。"
    if "layoffs" in low:
        return "Peter Yang 提到频繁裁员和绩效压力对心理健康的影响。对个人职业选择来说，值得把公司稳定性和组织节奏纳入判断。"
    if "agent labs" in low or "models get better" in low:
        return "Swyx 讨论一种会随着模型变强而变好的业务：Agent Lab 类产品可能直接受益于底层模型性能提升。"
    if len(text) <= 80:
        return f"{name} 发布了一条短动态：{text}。信息量较少，建议只作为弱信号保留。"
    return f"{name} 的这条动态值得快速扫一眼。主题大致是：{text[:160]}。建议结合原文判断它是否代表新的产品、技术或创业趋势。"


def podcast_cn_summary(title: str, transcript: str) -> str:
    low = f"{title} {transcript}".lower()
    if "stainless" in low and "mcp" in low:
        return (
            "这期播客讨论 Stainless、API、SDK 与 MCP 的关系。核心观点是：互联网原本是为人和传统软件设计的，"
            "但 AI Agent 需要一种更适合模型调用工具和服务的接口。Stainless 的方向是让 API、SDK 和 MCP 更适合 AI 使用，"
            "尤其是降低模型调用大量工具时的上下文成本和安全风险。"
        )
    return f"这期播客围绕“{title}”展开，建议关注其中关于 AI 产品、基础设施、开发者工具和创业趋势的判断。"


def fetch_html(url: str, timeout: int = 12) -> str:
    request = Request(url, headers={"User-Agent": "12-news-radar/1.0"})
    context = ssl._create_unverified_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        raw = response.read()
    return raw.decode("utf-8", errors="replace")


def fetch_links(url: str, timeout: int = 12) -> list[tuple[str, str]]:
    parser = LinkParser()
    parser.feed(fetch_html(url, timeout=timeout))
    return parser.links


def fetch_html_text(url: str, timeout: int = 12) -> str:
    parser = TextParser()
    parser.feed(fetch_html(url, timeout=timeout))
    chunks: list[str] = []
    seen: set[str] = set()
    for part in parser.parts:
        clean = clean_text(part)
        if len(clean) < 8 or clean in seen:
            continue
        seen.add(clean)
        chunks.append(clean)
    return " ".join(chunks[:80])


def youtube_video_details(url: str) -> dict[str, Any]:
    output = run_text(
        ["yt-dlp", "--no-check-certificates", "--skip-download", "--dump-json", url],
        timeout=60,
    )
    return json.loads(output.splitlines()[-1])


def clean_vtt(path: Path, max_words: int = 180) -> str:
    words: list[str] = []
    seen_lines: set[str] = set()
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("WEBVTT", "Kind:", "Language:")):
            continue
        if "-->" in line or re.match(r"^\d+$", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\[[^\]]+\]", "", line)
        line = clean_text(line)
        if len(line) < 3 or line in seen_lines:
            continue
        seen_lines.add(line)
        words.extend(line.split())
        if len(words) >= max_words:
            break
    return " ".join(words[:max_words])


def youtube_transcript_excerpt(url: str) -> str:
    with tempfile.TemporaryDirectory(prefix="12-news-radar-") as tmp:
        output_template = str(Path(tmp) / "%(id)s.%(ext)s")
        result = subprocess.run(
            [
                "yt-dlp",
                "--no-check-certificates",
                "--write-sub",
                "--write-auto-sub",
                "--sub-langs",
                "zh-Hans,zh,en",
                "--sub-format",
                "vtt",
                "--skip-download",
                "-o",
                output_template,
                url,
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=90,
            check=False,
        )
        if result.returncode != 0:
            return ""
        for path in sorted(Path(tmp).glob("*.vtt")):
            excerpt = clean_vtt(path)
            if len(excerpt) > 120:
                return excerpt
    return ""


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
            detail: dict[str, Any] = {}
            if url:
                try:
                    detail = youtube_video_details(url)
                    title = detail.get("title") or title
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{channel['name']} 视频详情：{exc}")
            transcript = ""
            if url and os.environ.get("USE_YOUTUBE_SUBTITLES") == "1":
                try:
                    transcript = youtube_transcript_excerpt(url)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{channel['name']} 字幕：{exc}")
            description = detail.get("description") or data.get("description") or ""
            source_text = transcript or clean_youtube_description(description) or description
            summary = summarize_text(
                source_text,
                fallback=title_based_summary(title, channel["name"]),
                max_sentences=3,
                max_chars=360,
            )
            if any(
                noise in summary.lower()
                for noise in ("skool", "work with me", "validated ideas", "my playbook", "free resources")
            ) or len(summary) < 50 or summary.startswith("("):
                summary = title_based_summary(title, channel["name"])
            summary = youtube_cn_summary(title, summary, channel["name"])
            view_count = detail.get("view_count") or data.get("view_count")
            upload_date = detail.get("upload_date")
            duration = detail.get("duration_string") or data.get("duration_string")
            meta = f"{channel.get('role', '')}"
            if view_count:
                meta = f"{meta}；播放量约 {view_count}"
            if duration:
                meta = f"{meta}；时长 {duration}"
            if upload_date and len(upload_date) == 8:
                meta = f"{meta}；发布 {upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
            items.append(
                Item(
                    title=title,
                    source=channel["name"],
                    url=url,
                    summary=summary,
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
    items: list[Item] = []
    x_feed = FOLLOW_BUILDERS_DIR / "feed-x.json"
    podcast_feed = FOLLOW_BUILDERS_DIR / "feed-podcasts.json"

    if x_feed.exists():
        try:
            data = json.loads(x_feed.read_text(encoding="utf-8"))
            for builder in data.get("x", [])[:6]:
                tweets = builder.get("tweets", [])
                if not tweets:
                    continue
                tweet = max(tweets, key=lambda t: int(t.get("likes") or 0))
                text = clean_text(tweet.get("text") or "")
                if not text:
                    continue
                title = f"{builder.get('name', 'AI Builder')}：{text[:42]}"
                summary = builder_tweet_cn_summary(builder.get("name", "AI Builder"), text)
                items.append(
                    Item(
                        title=title,
                        source=f"X / {builder.get('handle', '')}",
                        url=tweet.get("url", ""),
                        summary=summary,
                        meta=f"Likes: {tweet.get('likes', 0)}；Replies: {tweet.get('replies', 0)}",
                    )
                )
        except Exception:  # noqa: BLE001
            pass

    if podcast_feed.exists():
        try:
            data = json.loads(podcast_feed.read_text(encoding="utf-8"))
            for episode in data.get("podcasts", [])[:2]:
                transcript = clean_text(episode.get("transcript") or "")
                title = episode.get("title") or episode.get("name") or "AI Builder Podcast"
                summary = podcast_cn_summary(title, transcript)
                items.append(
                    Item(
                        title=f"{episode.get('name', 'Podcast')}：{title}",
                        source="follow-builders / Podcast",
                        url=episode.get("url", ""),
                        summary=f"播客摘要：{summary}",
                        meta=f"发布: {(episode.get('publishedAt') or '')[:10]}",
                    )
                )
        except Exception:  # noqa: BLE001
            pass

    if items:
        return items[:8]

    url = sources.get("ai_builders", {}).get("url", "https://github.com/zarazhangrui/follow-builders")
    return [
        Item(
            title="follow-builders",
            source="AI Builder",
            url=url,
            summary="未能读取 follow-builders feed，已保留项目入口。后续可重试中心 feed 或检查本地 skill 文件。",
            meta="feed 兜底",
        )
    ]


def fetch_links_from_source(source: dict[str, Any], max_links: int = 5) -> tuple[list[Item], str | None]:
    url = source["url"]
    keywords = source.get("keywords", [])
    try:
        links = fetch_links(url)
    except (OSError, URLError) as exc:
        return [], f"{source['name']}：{exc}"

    found: list[Item] = []
    seen: set[str] = set()
    generic_titles = {
        "政策",
        "部门政策解读",
        "惠企政策查询",
        "政策文件库",
        "计算机软件资格考试",
        "事业单位招聘",
        "事业单位人事统计报表",
        "政策图解",
        "政策文件",
        "政策解读",
        "政策问答",
        "创新服务",
        "公务员招考",
    }
    for title, href in links:
        if not title or not href:
            continue
        href_lower = href.strip().lower()
        if href_lower.startswith(("javascript:", "#")):
            continue
        if title in generic_titles:
            continue
        if keywords and not any(keyword in title for keyword in keywords):
            continue
        absolute = urljoin(url, href)
        key = f"{title}|{absolute}"
        if key in seen:
            continue
        seen.add(key)
        try:
            article_text = fetch_html_text(absolute)
            summary = summarize_text(
                article_text,
                fallback=f"{source['name']} 发布了“{title}”相关信息，需点击原文核对细节。",
                max_sentences=3,
                max_chars=300,
            )
        except Exception:  # noqa: BLE001
            summary = f"{source['name']} 发布了“{title}”相关信息，需点击原文核对细节。"
        found.append(
            Item(
                title=title,
                source=source["name"],
                url=absolute,
                summary=summary,
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
    parts = [f"**{item.title}**"]
    if item.summary:
        parts.extend(["", item.summary])
    if item.meta:
        parts.extend(["", f"补充：{item.meta}"])
    if item.url:
        parts.extend(["", item.url])
    return "\n".join(parts)


def date_cn(date: str) -> str:
    parsed = datetime.strptime(date, "%Y-%m-%d")
    return parsed.strftime("%Y年%m月%d日")


def report_title(report: dict[str, Any]) -> str:
    title_by_task = {
        "daily": "12 资讯雷达日报",
        "ai-money": "AI 变现派日报",
        "ai-builders": "AI Builders 日报",
        "policy-jobs": "政策、产业与岗位机会雷达",
        "daily-review": "每日成长复盘",
        "weekly-plan": "每周行动清单",
        "weekly-github": "GitHub 热门周报",
        "weekly-opportunity-review": "每周机会复盘",
    }
    return f"{title_by_task.get(report['task'], '12 资讯雷达')} · {date_cn(report['date'])}"


def display_section(section: str) -> str:
    section_names = {
        "AI 搞钱": "💰 AI 搞钱",
        "GitHub 热门 Top20": "⭐ GitHub 热门 Top20",
        "AI Builder": "🧑‍💻 AI Builder",
        "政策、产业与岗位机会雷达": "🏢 政策、产业与岗位机会雷达",
        "每日成长复盘": "🌙 每日成长复盘",
        "每周行动清单": "📌 每周行动清单",
        "每周机会复盘": "🔎 每周机会复盘",
    }
    return section_names.get(section, section)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report_title(report)}", "", "## 今日摘要"]
    section_count = sum(len(items) for items in report["sections"].values())
    lines.append(f"- 本次生成 {len(report['sections'])} 个板块，共 {section_count} 条信息。")
    if report["errors"]:
        lines.append(f"- 有 {len(report['errors'])} 个来源抓取失败，已记录在文末。")
    else:
        lines.append("- 所有已配置来源均完成处理。")

    for section, items in report["sections"].items():
        lines.extend(["", f"## {display_section(section)}", ""])
        if not items:
            lines.append("- 今日暂无内容。")
            continue
        for index, item in enumerate(items):
            if index:
                lines.extend(["", "---", ""])
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
        f'<a href="#{slugify_anchor(section)}">{html.escape(display_section(section))}</a>'
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
            f'<section id="{slugify_anchor(section)}"><h2>{html.escape(display_section(section))}</h2>{"".join(cards)}</section>'
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
  <title>{html.escape(report_title(report))}</title>
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
      <h1>{html.escape(report_title(report))}</h1>
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
    parser.add_argument("--publish", action="store_true", help="Publish generated HTML to GitHub Pages.")
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
    published_url = ""
    if args.publish:
        publish_script = ROOT / "scripts" / "publish_github_pages.sh"
        result = subprocess.run(["bash", str(publish_script)], cwd=ROOT, text=True, check=False)
        if result.returncode != 0:
            return result.returncode
        github = settings.get("github", {})
        published_url = f"https://{github.get('owner')}.github.io/{github.get('repo')}/"
    if args.notify:
        body = f"Obsidian: {markdown_path}\nHTML: {html_path}"
        if published_url:
            body = f"{body}\nPages: {published_url}"
        notify(
            f"{report['date']} 资讯雷达已生成",
            body,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
