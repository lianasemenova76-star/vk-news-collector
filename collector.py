#!/usr/bin/env python3
"""Collect recent public posts from configured VK communities and public profiles."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://api.vk.com/method/wall.get"
API_VERSION = "5.199"
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"


def api_call(token: str, domain: str, offset: int = 0) -> dict:
    params = urlencode(
        {
            "access_token": token,
            "v": API_VERSION,
            "domain": domain,
            "count": 100,
            "offset": offset,
            "filter": "owner",
            "extended": 1,
        }
    )
    request = Request(f"{API_URL}?{params}", headers={"User-Agent": "vk-news-collector/1.1"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"VK request failed for {domain}: {exc}") from exc

    if "error" in payload:
        error = payload["error"]
        raise RuntimeError(
            f"VK API error {error.get('error_code')} for {domain}: "
            f"{error.get('error_msg', 'unknown error')}"
        )
    return payload["response"]


def source_name_from_response(source: dict, response: dict) -> str:
    groups = response.get("groups") or []
    if groups:
        return groups[0].get("name") or source["domain"]

    profiles = response.get("profiles") or []
    if profiles:
        profile = profiles[0]
        full_name = " ".join(
            part for part in (profile.get("first_name"), profile.get("last_name")) if part
        )
        if full_name:
            return full_name

    return source.get("name") or source["domain"]


def best_photo_url(photo: dict) -> str | None:
    sizes = photo.get("sizes") or []
    if not sizes:
        return None
    best = max(sizes, key=lambda item: item.get("width", 0) * item.get("height", 0))
    return best.get("url")


def parse_attachments(attachments: list[dict]) -> list[dict]:
    result: list[dict] = []
    for attachment in attachments or []:
        kind = attachment.get("type")
        item = attachment.get(kind, {}) if kind else {}
        parsed: dict = {"type": kind}

        if kind == "photo":
            parsed["url"] = best_photo_url(item)
            parsed["width"] = item.get("width")
            parsed["height"] = item.get("height")
        elif kind == "video":
            owner_id = item.get("owner_id")
            video_id = item.get("id")
            parsed.update(
                {
                    "title": item.get("title"),
                    "duration": item.get("duration"),
                    "url": f"https://vk.ru/video{owner_id}_{video_id}" if owner_id and video_id else None,
                }
            )
        elif kind == "link":
            parsed.update({"url": item.get("url"), "title": item.get("title")})
        elif kind == "doc":
            parsed.update({"url": item.get("url"), "title": item.get("title")})
        else:
            parsed["id"] = item.get("id")

        result.append({key: value for key, value in parsed.items() if value is not None})
    return result


def post_url(owner_id: int, post_id: int) -> str:
    return f"https://vk.ru/wall{owner_id}_{post_id}"


def parse_post(post: dict, source: dict) -> dict:
    owner_id = post["owner_id"]
    post_id = post["id"]
    published = datetime.fromtimestamp(post["date"], tz=timezone.utc)
    repost = post.get("copy_history") or []
    return {
        "id": f"{owner_id}_{post_id}",
        "source": source["domain"],
        "source_name": source.get("name") or source["domain"],
        "published_at": published.isoformat(),
        "text": post.get("text", ""),
        "url": post_url(owner_id, post_id),
        "is_pinned": bool(post.get("is_pinned")),
        "metrics": {
            "views": (post.get("views") or {}).get("count"),
            "likes": (post.get("likes") or {}).get("count"),
            "reposts": (post.get("reposts") or {}).get("count"),
            "comments": (post.get("comments") or {}).get("count"),
        },
        "attachments": parse_attachments(post.get("attachments") or []),
        "repost": parse_post(repost[0], source) if repost else None,
    }


def collect_source(token: str, source: dict, cutoff_timestamp: int) -> list[dict]:
    domain = source["domain"]
    resolved_source = dict(source)
    collected: list[dict] = []
    offset = 0

    for page_number in range(10):
        response = api_call(token, domain, offset)
        if page_number == 0:
            resolved_source["name"] = source_name_from_response(source, response)

        items = response.get("items", [])
        if not items:
            break

        collected.extend(
            parse_post(post, resolved_source)
            for post in items
            if post.get("date", 0) >= cutoff_timestamp
        )

        ordinary_posts = [post for post in items if not post.get("is_pinned")]
        if ordinary_posts and min(post.get("date", 0) for post in ordinary_posts) < cutoff_timestamp:
            break
        if len(items) < 100:
            break

        offset += len(items)
        time.sleep(0.4)

    unique = {post["id"]: post for post in collected}
    return sorted(unique.values(), key=lambda post: post["published_at"], reverse=True)


def main() -> int:
    token = os.environ.get("VK_SERVICE_TOKEN")
    if not token:
        print("VK_SERVICE_TOKEN is not set", file=sys.stderr)
        return 1

    with (ROOT / "sources.json").open(encoding="utf-8") as file:
        sources = json.load(file)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    posts: list[dict] = []
    errors: list[dict] = []

    for index, source in enumerate(sources):
        try:
            posts.extend(collect_source(token, source, int(cutoff.timestamp())))
        except Exception as exc:  # Keep other sources working if one fails.
            errors.append({"source": source["domain"], "error": str(exc)})

        if index < len(sources) - 1:
            time.sleep(0.4)

    posts.sort(key=lambda post: post["published_at"], reverse=True)
    document = {
        "generated_at": now.isoformat(),
        "period_start": cutoff.isoformat(),
        "period_end": now.isoformat(),
        "source_count": len(sources),
        "post_count": len(posts),
        "errors": errors,
        "posts": posts,
    }

    DATA_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(exist_ok=True)
    serialized = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    (DATA_DIR / "latest.json").write_text(serialized, encoding="utf-8")
    archive_name = now.strftime("%Y-%m-%d") + ".json"
    (ARCHIVE_DIR / archive_name).write_text(serialized, encoding="utf-8")

    print(f"Collected {len(posts)} posts from {len(sources)} source(s)")
    if errors:
        print(json.dumps(errors, ensure_ascii=False), file=sys.stderr)
        if len(errors) == len(sources):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
