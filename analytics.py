#!/usr/bin/env python3
"""Collect public engagement counters for posts on the configured VK wall."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


API_BASE = "https://api.vk.com/method"
API_VERSION = "5.199"
GROUP_DOMAIN = "vtutaeve"
MOSCOW = ZoneInfo("Europe/Moscow")


def api_call(method: str, token: str, **params: object) -> object:
    payload = {"access_token": token, "v": API_VERSION, **params}
    request = Request(
        f"{API_BASE}/{method}",
        data=urlencode(payload).encode("utf-8"),
        headers={"User-Agent": "vk-news-collector-analytics/2.0"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            document = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"VK request failed for {method}: {exc}") from exc
    if "error" in document:
        error = document["error"]
        raise RuntimeError(
            f"VK API error {error.get('error_code')} for {method}: "
            f"{error.get('error_msg', 'unknown error')}"
        )
    return document.get("response")


def extract_groups(response: object) -> list[dict]:
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        groups = response.get("groups") or response.get("items") or []
        return groups if isinstance(groups, list) else []
    return []


def resolve_group(token: str) -> dict:
    groups = extract_groups(api_call("groups.getById", token, group_ids=GROUP_DOMAIN))
    if not groups or not groups[0].get("id"):
        raise RuntimeError(f"VK did not return group {GROUP_DOMAIN}")
    return groups[0]


def collect_posts(token: str, owner_id: int, start_ts: int, end_ts: int) -> list[dict]:
    posts: list[dict] = []
    offset = 0
    while True:
        response = api_call(
            "wall.get",
            token,
            owner_id=owner_id,
            count=100,
            offset=offset,
            filter="owner",
            extended=0,
        )
        items = response.get("items", []) if isinstance(response, dict) else []
        if not items:
            break
        for post in items:
            published = int(post.get("date", 0))
            if start_ts <= published < end_ts:
                posts.append(post)

        ordinary_dates = [
            int(post.get("date", 0)) for post in items if not post.get("is_pinned")
        ]
        if ordinary_dates and min(ordinary_dates) < start_ts:
            break
        if len(items) < 100:
            break
        offset += len(items)
        time.sleep(0.25)

    unique = {int(post["id"]): post for post in posts}
    return sorted(unique.values(), key=lambda post: int(post["date"]), reverse=True)


def first_line(text: str) -> str:
    title = next((line.strip() for line in text.splitlines() if line.strip()), "Без заголовка")
    return title[:160]


def count(post: dict, field: str) -> int:
    value = post.get(field) or {}
    return int(value.get("count") or 0) if isinstance(value, dict) else 0


def normalize_post(post: dict) -> dict:
    views = count(post, "views")
    likes = count(post, "likes")
    reposts = count(post, "reposts")
    comments = count(post, "comments")
    interactions = likes + reposts + comments
    published = datetime.fromtimestamp(int(post["date"]), tz=MOSCOW)
    owner_id = int(post["owner_id"])
    post_id = int(post["id"])
    return {
        "post_id": post_id,
        "published_at": published.isoformat(timespec="minutes"),
        "title": first_line(str(post.get("text") or "")),
        "url": f"https://vk.ru/wall{owner_id}_{post_id}",
        "views": views,
        "likes": likes,
        "reposts": reposts,
        "comments": comments,
        "interactions": interactions,
        "engagement_by_views_percent": round(interactions * 100 / views, 2) if views else None,
    }


def totals(posts: list[dict]) -> dict:
    result = {
        "posts": len(posts),
        "views": sum(post["views"] for post in posts),
        "likes": sum(post["likes"] for post in posts),
        "reposts": sum(post["reposts"] for post in posts),
        "comments": sum(post["comments"] for post in posts),
        "interactions": sum(post["interactions"] for post in posts),
    }
    result["engagement_by_views_percent"] = (
        round(result["interactions"] * 100 / result["views"], 2)
        if result["views"]
        else None
    )
    return result


def render_markdown(document: dict) -> str:
    summary = document["totals"]
    lines = [
        f"# Статистика VK за {document['period_start']} - {document['period_end']}",
        "",
        f"Группа: [{document['group']['name']}](https://vk.ru/{document['group']['domain']})",
        "",
        "| Публикации | Просмотры | Лайки | Репосты | Комментарии | Вовлечённость от просмотров |",
        "|---:|---:|---:|---:|---:|---:|",
        (
            f"| {summary['posts']} | {summary['views']} | {summary['likes']} | "
            f"{summary['reposts']} | {summary['comments']} | "
            f"{summary['engagement_by_views_percent'] or 0:.2f}% |"
        ),
        "",
        "## Публикации по числу взаимодействий",
        "",
        "| № | Дата и время | Публикация | Просмотры | Лайки | Репосты | Комментарии | ER |",
        "|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    ranked = sorted(
        document["posts"],
        key=lambda item: (item["interactions"], item["views"]),
        reverse=True,
    )
    for index, post in enumerate(ranked, 1):
        published = datetime.fromisoformat(post["published_at"]).strftime("%d.%m %H:%M")
        title = post["title"].replace("|", "\\|")
        er = post["engagement_by_views_percent"] or 0
        lines.append(
            f"| {index} | {published} | [{title}]({post['url']}) | {post['views']} | "
            f"{post['likes']} | {post['reposts']} | {post['comments']} | {er:.2f}% |"
        )
    if not ranked:
        lines.append("| 1 | - | Публикаций за период нет | 0 | 0 | 0 | 0 | 0.00% |")
    lines.append("")
    lines.append("ER рассчитан как (лайки + репосты + комментарии) / просмотры × 100%.")
    lines.append("")
    return "\n".join(lines)


def write_reports(document: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "report.md").write_text(render_markdown(document), encoding="utf-8")
    with (output_dir / "posts.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "published_at", "title", "url", "views", "likes", "reposts", "comments",
            "interactions", "engagement_by_views_percent",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(document["posts"])


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True, type=parse_date)
    parser.add_argument("--end-date", required=True, type=parse_date)
    parser.add_argument("--output-dir", type=Path, default=Path("analytics-report"))
    args = parser.parse_args()
    if args.end_date < args.start_date:
        parser.error("--end-date must not be earlier than --start-date")
    if (args.end_date - args.start_date).days > 31:
        parser.error("period must not exceed 32 calendar days")

    token = os.environ.get("VK_ANALYTICS_TOKEN") or os.environ.get("VK_PUBLISH_TOKEN")
    if not token:
        print("VK_ANALYTICS_TOKEN or VK_PUBLISH_TOKEN is not set", file=sys.stderr)
        return 1

    period_start = datetime.combine(args.start_date, datetime_time.min, tzinfo=MOSCOW)
    period_end_exclusive = datetime.combine(
        args.end_date + timedelta(days=1), datetime_time.min, tzinfo=MOSCOW
    )
    try:
        group = resolve_group(token)
        group_id = int(group["id"])
        raw_posts = collect_posts(
            token,
            -group_id,
            int(period_start.timestamp()),
            int(period_end_exclusive.timestamp()),
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    posts = [normalize_post(post) for post in raw_posts]
    document = {
        "generated_at": datetime.now(MOSCOW).isoformat(timespec="seconds"),
        "period_start": args.start_date.isoformat(),
        "period_end": args.end_date.isoformat(),
        "period_note": "The end date may be a partial day if the report is run before midnight Moscow time.",
        "group": {
            "id": group_id,
            "domain": GROUP_DOMAIN,
            "name": group.get("name") or GROUP_DOMAIN,
        },
        "totals": totals(posts),
        "posts": posts,
    }
    write_reports(document, args.output_dir)
    print(f"Collected {len(posts)} posts for {args.start_date} - {args.end_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
