#!/usr/bin/env python3
"""Collect comments from recent posts on the configured VK community wall."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from analytics import API_BASE, API_VERSION, GROUP_DOMAIN, api_call, resolve_group

MOSCOW = ZoneInfo("Europe/Moscow")


def collect_recent_posts(token: str, owner_id: int, since_ts: int) -> list[dict]:
    posts: list[dict] = []
    offset = 0
    while True:
        response = api_call(
            "wall.get", token, owner_id=owner_id, count=100, offset=offset,
            filter="owner", extended=0,
        )
        items = response.get("items", []) if isinstance(response, dict) else []
        if not items:
            break
        posts.extend(post for post in items if int(post.get("date", 0)) >= since_ts)
        ordinary = [int(post.get("date", 0)) for post in items if not post.get("is_pinned")]
        if len(items) < 100 or (ordinary and min(ordinary) < since_ts):
            break
        offset += len(items)
        time.sleep(0.25)
    return list({int(post["id"]): post for post in posts}.values())


def collect_post_comments(token: str, owner_id: int, post_id: int) -> tuple[list[dict], dict[int, dict]]:
    comments: list[dict] = []
    profiles: dict[int, dict] = {}
    offset = 0
    while True:
        response = api_call(
            "wall.getComments", token, owner_id=owner_id, post_id=post_id,
            need_likes=1, extended=1, count=100, offset=offset, sort="asc",
            thread_items_count=10,
        )
        if not isinstance(response, dict):
            break
        items = response.get("items", [])
        comments.extend(items)
        for profile in response.get("profiles", []):
            profiles[int(profile["id"])] = profile
        for group in response.get("groups", []):
            profiles[-int(group["id"])] = group
        if len(items) < 100:
            break
        offset += len(items)
        time.sleep(0.25)
    return comments, profiles


def author_name(author_id: int, profiles: dict[int, dict]) -> str:
    profile = profiles.get(author_id, {})
    if author_id < 0:
        return str(profile.get("name") or f"Сообщество {abs(author_id)}")
    name = " ".join(filter(None, [profile.get("first_name"), profile.get("last_name")]))
    return name or f"Пользователь {author_id}"


def attachment_summary(comment: dict) -> list[str]:
    return [str(item.get("type")) for item in comment.get("attachments", []) if item.get("type")]


def normalize_comment(comment: dict, owner_id: int, post_id: int, profiles: dict[int, dict]) -> dict:
    comment_id = int(comment["id"])
    author_id = int(comment.get("from_id") or 0)
    return {
        "comment_id": comment_id,
        "post_id": post_id,
        "published_at": datetime.fromtimestamp(int(comment["date"]), tz=MOSCOW).isoformat(timespec="minutes"),
        "author_id": author_id,
        "author": author_name(author_id, profiles),
        "text": str(comment.get("text") or "").strip(),
        "likes": int((comment.get("likes") or {}).get("count") or 0),
        "attachments": attachment_summary(comment),
        "reply_count": int((comment.get("thread") or {}).get("count") or 0),
        "url": f"https://vk.ru/wall{owner_id}_{post_id}?reply={comment_id}",
    }


def render_markdown(document: dict) -> str:
    lines = [
        "# Новые комментарии VK",
        "",
        f"Собрано: {document['generated_at']}",
        f"Проверено публикаций: {document['posts_checked']}",
        f"Новых комментариев: {len(document['comments'])}",
        "",
    ]
    for index, item in enumerate(document["comments"], 1):
        text = item["text"] or "[без текста]"
        attachments = ", ".join(item["attachments"])
        lines.extend([
            f"## {index}. {item['author']}", "",
            f"{item['published_at']} · [открыть комментарий]({item['url']})",
            "", text,
        ])
        if attachments:
            lines.extend(["", f"Вложения: {attachments}"])
        lines.append("")
    if not document["comments"]:
        lines.extend(["Новых комментариев нет.", ""])
    return "\n".join(lines)


def load_checkpoint(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("last_comment_timestamp") or 0)
    except (ValueError, TypeError, json.JSONDecodeError):
        return 0


def token_from_environment() -> str | None:
    return (
        os.environ.get("VK_SERVICE_TOKEN")
        or os.environ.get("VK_ANALYTICS_TOKEN")
        or os.environ.get("VK_USER_TOKEN")
        or os.environ.get("VK_PUBLISH_TOKEN")
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--output-dir", type=Path, default=Path("comments-report"))
    parser.add_argument("--checkpoint", type=Path)
    args = parser.parse_args()
    if not 1 <= args.days <= 31:
        parser.error("--days must be between 1 and 31")
    token = token_from_environment()
    if not token:
        print("VK token is not set", file=sys.stderr)
        return 1
    now = datetime.now(MOSCOW)
    since_ts = int((now - timedelta(days=args.days)).timestamp())
    checkpoint = load_checkpoint(args.checkpoint) if args.checkpoint else 0
    try:
        group = resolve_group(token)
        owner_id = -int(group["id"])
        posts = collect_recent_posts(token, owner_id, since_ts)
        normalized: list[dict] = []
        newest_seen = checkpoint
        for post in posts:
            comments, profiles = collect_post_comments(token, owner_id, int(post["id"]))
            for comment in comments:
                candidates = [comment, *((comment.get("thread") or {}).get("items") or [])]
                for candidate in candidates:
                    published = int(candidate.get("date") or 0)
                    newest_seen = max(newest_seen, published)
                    if published > checkpoint:
                        normalized.append(normalize_comment(candidate, owner_id, int(post["id"]), profiles))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    normalized.sort(key=lambda item: item["published_at"], reverse=True)
    document = {
        "generated_at": now.isoformat(timespec="seconds"),
        "group": {"id": abs(owner_id), "domain": GROUP_DOMAIN, "name": group.get("name")},
        "lookback_days": args.days,
        "posts_checked": len(posts),
        "previous_checkpoint": checkpoint or None,
        "comments": normalized,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "latest.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "latest.md").write_text(render_markdown(document), encoding="utf-8")
    if args.checkpoint:
        args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        args.checkpoint.write_text(json.dumps({"last_comment_timestamp": newest_seen}, indent=2) + "\n", encoding="utf-8")
    print(f"Collected {len(normalized)} new comments from {len(posts)} posts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
