#!/usr/bin/env python3
"""Collect a compact performance report for the VK community wall."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


API_BASE = "https://api.vk.com/method"
API_VERSION = "5.199"
GROUP_DOMAIN = "vtutaeve"
MOSCOW = ZoneInfo("Europe/Moscow")
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "data" / "analytics"


def api_call(method: str, token: str, **params: object) -> object:
    payload = {"access_token": token, "v": API_VERSION, **params}
    request = Request(
        f"{API_BASE}/{method}",
        data=urlencode(payload).encode("utf-8"),
        headers={"User-Agent": "vk-news-collector-analytics/1.0"},
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


def resolve_group(token: str) -> dict:
    response = api_call("groups.getById", token, group_ids=GROUP_DOMAIN, fields="members_count")
    groups = response if isinstance(response, list) else (response or {}).get("groups", [])
    if not groups:
        raise RuntimeError(f"VK did not return group {GROUP_DOMAIN}")
    return groups[0]


def collect_posts(token: str, owner_id: int, start_ts: int, end_ts: int) -> list[dict]:
    posts: list[dict] = []
    offset = 0
    for _ in range(10):
        response = api_call(
            "wall.get", token, owner_id=owner_id, count=100, offset=offset,
            filter="owner", extended=0,
        )
        items = (response or {}).get("items", [])
        if not items:
            break
        for post in items:
            published = int(post.get("date", 0))
            if start_ts <= published <= end_ts:
                posts.append(post)
        ordinary = [post for post in items if not post.get("is_pinned")]
        if ordinary and min(int(post.get("date", 0)) for post in ordinary) < start_ts:
            break
        if len(items) < 100:
            break
        offset += len(items)
        time.sleep(0.35)
    unique = {int(post["id"]): post for post in posts}
    return sorted(unique.values(), key=lambda post: int(post["date"]))


def collect_commenters(token: str, owner_id: int, post_id: int) -> tuple[int, list[int]]:
    authors: set[int] = set()
    total = 0
    offset = 0
    while True:
        response = api_call(
            "wall.getComments", token, owner_id=owner_id, post_id=post_id,
            count=100, offset=offset, sort="asc", thread_items_count=10,
        )
        total = int((response or {}).get("count", 0))
        items = (response or {}).get("items", [])
        for comment in items:
            if comment.get("from_id"):
                authors.add(int(comment["from_id"]))
            for reply in (comment.get("thread") or {}).get("items", []):
                if reply.get("from_id"):
                    authors.add(int(reply["from_id"]))
        offset += len(items)
        if not items or offset >= total:
            break
        time.sleep(0.35)
    return total, sorted(authors)


def safe_call(errors: list[dict], name: str, function) -> object | None:
    try:
        return function()
    except Exception as exc:
        errors.append({"metric": name, "error": str(exc)})
        return None


def post_title(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "Без заголовка")
    return first[:140]


def normalize_post(post: dict, commenters: tuple[int, list[int]], reach: object | None) -> dict:
    views = int((post.get("views") or {}).get("count") or 0)
    likes = int((post.get("likes") or {}).get("count") or 0)
    reposts = int((post.get("reposts") or {}).get("count") or 0)
    comments = int((post.get("comments") or {}).get("count") or commenters[0] or 0)
    interactions = likes + reposts + comments
    published = datetime.fromtimestamp(int(post["date"]), tz=timezone.utc).astimezone(MOSCOW)
    reach_data = reach[0] if isinstance(reach, list) and reach else reach
    if not isinstance(reach_data, dict):
        reach_data = None
    return {
        "post_id": int(post["id"]),
        "url": f"https://vk.ru/wall{post['owner_id']}_{post['id']}",
        "published_at": published.isoformat(),
        "title": post_title(post.get("text", "")),
        "views": views,
        "likes": likes,
        "reposts": reposts,
        "comments": comments,
        "unique_commenters": len(commenters[1]),
        "commenter_ids": commenters[1],
        "engagement_by_views_percent": round(interactions / views * 100, 2) if views else None,
        "reach": reach_data,
    }


def aggregate(posts: list[dict]) -> dict:
    totals = {
        "posts": len(posts),
        "views": sum(post["views"] for post in posts),
        "likes": sum(post["likes"] for post in posts),
        "reposts": sum(post["reposts"] for post in posts),
        "comments": sum(post["comments"] for post in posts),
        "unique_commenters": len({item for post in posts for item in post["commenter_ids"]}),
    }
    interactions = totals["likes"] + totals["reposts"] + totals["comments"]
    totals["engagement_by_views_percent"] = (
        round(interactions / totals["views"] * 100, 2) if totals["views"] else None
    )
    hourly = Counter(datetime.fromisoformat(post["published_at"]).hour for post in posts)
    totals["publication_hours"] = dict(sorted(hourly.items()))
    return totals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()
    if args.days < 1 or args.days > 31:
        parser.error("--days must be between 1 and 31")

    token = os.environ.get("VK_ANALYTICS_TOKEN") or os.environ.get("VK_PUBLISH_TOKEN")
    if not token:
        print("VK_ANALYTICS_TOKEN or VK_PUBLISH_TOKEN is not set", file=sys.stderr)
        return 1

    now = datetime.now(MOSCOW)
    start_day = now.date() - timedelta(days=args.days - 1)
    period_start = datetime.combine(start_day, datetime_time.min, tzinfo=MOSCOW)
    errors: list[dict] = []

    try:
        group = resolve_group(token)
        group_id = int(group["id"])
        owner_id = -group_id
        raw_posts = collect_posts(token, owner_id, int(period_start.timestamp()), int(now.timestamp()))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    posts: list[dict] = []
    for raw_post in raw_posts:
        post_id = int(raw_post["id"])
        commenters = safe_call(
            errors, f"comments:{post_id}",
            lambda post_id=post_id: collect_commenters(token, owner_id, post_id),
        ) or (int((raw_post.get("comments") or {}).get("count") or 0), [])
        reach = safe_call(
            errors, f"reach:{post_id}",
            lambda post_id=post_id: api_call(
                "stats.getPostReach", token, owner_id=owner_id, post_ids=post_id,
            ),
        )
        posts.append(normalize_post(raw_post, commenters, reach))
        time.sleep(0.35)

    group_stats = safe_call(
        errors, "group_stats",
        lambda: api_call(
            "stats.get", token, group_id=group_id, interval="day",
            timestamp_from=int(period_start.timestamp()), timestamp_to=int(now.timestamp()),
        ),
    )

    ranking_views = sorted(posts, key=lambda item: item["views"], reverse=True)
    ranking_engagement = sorted(
        posts,
        key=lambda item: (item["comments"] + item["likes"] + item["reposts"], item["views"]),
        reverse=True,
    )
    document = {
        "generated_at": now.isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": now.isoformat(),
        "period_note": "Current Moscow calendar day is partial.",
        "group": {
            "id": group_id,
            "domain": GROUP_DOMAIN,
            "name": group.get("name"),
            "members_count_current": group.get("members_count"),
        },
        "totals": aggregate(posts),
        "top_by_views": ranking_views[:5],
        "top_by_interactions": ranking_engagement[:5],
        "posts": posts,
        "group_stats": group_stats,
        "errors": errors,
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    (REPORT_DIR / "latest.json").write_text(serialized, encoding="utf-8")
    archive_name = now.strftime("%Y-%m-%dT%H-%M-%S%z") + ".json"
    (REPORT_DIR / archive_name).write_text(serialized, encoding="utf-8")
    print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
