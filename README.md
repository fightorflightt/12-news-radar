# 12 News Radar

个人资讯摘要系统：生成 Obsidian Markdown、每日 HTML 页面，并发布到 GitHub Pages。

## 当前范围

- 每天 10:00：AI 搞钱、AI Builder、政策/产业/岗位机会雷达
- 每周一、周五 10:00：GitHub 热门 Top20
- 每天 21:00：每日成长复盘
- 每周一 09:40：每周行动清单
- 每周五 14:00：每周机会复盘

输出位置：

- Obsidian：`/Users/xiongbatian/Desktop/全栈学习笔记/12-资讯`
- HTML：`docs/`
- GitHub Pages：`https://fightorflightt.github.io/12-news-radar/`

## 使用方式

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

生成今日资讯：

```bash
python3 scripts/news_radar.py daily
```

生成指定日期：

```bash
python3 scripts/news_radar.py daily --date 2026-07-04
```

发布到 GitHub Pages：

```bash
bash scripts/publish_github_pages.sh
```

## 任务命令

| 任务 | 命令 |
|---|---|
| 每日资讯 | `python3 scripts/news_radar.py daily --publish --notify` |
| AI 搞钱 | `python3 scripts/news_radar.py ai-money` |
| AI Builder | `python3 scripts/news_radar.py ai-builders` |
| 政策/产业/岗位雷达 | `python3 scripts/news_radar.py policy-jobs` |
| 每日成长复盘 | `python3 scripts/news_radar.py daily-review --publish --notify` |
| 每周行动清单 | `python3 scripts/news_radar.py weekly-plan --publish --notify` |
| GitHub 热门 | `python3 scripts/news_radar.py weekly-github --publish --notify` |
| 每周机会复盘 | `python3 scripts/news_radar.py weekly-opportunity-review --publish --notify` |

## Hermes 通知

当前只预留 Hermes 通知接口，因为本机暂时没有找到 `hermes` 命令。

配置方式有两种：

1. 在 `config/settings.json` 填写：

```json
{
  "notification": {
    "provider": "hermes",
    "hermes_target": "weixin:xxx@im.wechat",
    "hermes_command": "你的 Hermes 发送命令"
  }
}
```

2. 或者运行前设置环境变量：

```bash
export HERMES_TARGET="weixin:xxx@im.wechat"
export HERMES_COMMAND="你的 Hermes 发送命令"
python3 scripts/news_radar.py daily --notify
```

`HERMES_COMMAND` 支持 `{title}`、`{body}`、`{target}` 三个占位符。

## 需要继续增强的地方

- 给政府/招聘来源补充更精确的栏目页或 RSS。
- 接入 follow-builders 的实际 feed。
- 如果需要更像“编辑部摘要”，再接入 LLM 摘要层。
