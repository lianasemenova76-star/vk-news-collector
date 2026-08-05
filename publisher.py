#!/usr/bin/env python3
"""Validate a VK community token and publish only with explicit confirmation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.vk.com/method"
API_VERSION = "5.199"
GROUP_DOMAIN = "vtutaeve"


def api_call(method: str, token: str, **params: object) -> object:
    payload = {
        "access_token": token,
        "v": API_VERSION,
        **params,
    }
    request = Request(
        f"{API_BASE}/{method}?{urlencode(payload)}",
        headers={"User-Agent": "vk-news-collector-publisher/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            document = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"VK request failed: {exc}") from exc

    if "error" in document:
        error = document["error"]
        raise RuntimeError(
            f"VK API error {error.get('error_code')}: "
            f"{error.get('error_msg', 'unknown error')}"
        )
    return document.get("response")


def extract_group(response: object) -> dict:
    if isinstance(response, list):
        groups = response
    elif isinstance(response, dict):
        groups = response.get("groups") or response.get("items") or []
    else:
        groups = []

    if not groups:
        raise RuntimeError(f"VK did not return group {GROUP_DOMAIN}")
    return groups[0]


def resolve_group(token: str) -> dict:
    response = api_call("groups.getById", token, group_ids=GROUP_DOMAIN)
    group = extract_group(response)
    group_id = group.get("id")
    if not group_id:
        raise RuntimeError("VK returned a group without an id")
    return group


def check_token(token: str) -> None:
    group = resolve_group(token)
    permissions = api_call("groups.getTokenPermissions", token)
    print(
        json.dumps(
            {
                "status": "ok",
                "group_id": group.get("id"),
                "group_name": group.get("name"),
                "group_domain": GROUP_DOMAIN,
                "permissions": permissions,
                "published": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def publish(token: str, message: str) -> None:
    if os.environ.get("CONFIRM_PUBLISH") != "YES":
        raise RuntimeError("Publishing is blocked: CONFIRM_PUBLISH must be YES")
    if not message.strip():
        raise RuntimeError("Publishing is blocked: message is empty")

    group = resolve_group(token)
    response = api_call(
        "wall.post",
        token,
        owner_id=-int(group["id"]),
        from_group=1,
        message=message.strip(),
    )
    post_id = response.get("post_id") if isinstance(response, dict) else None
    if not post_id:
        raise RuntimeError("VK did not return post_id after wall.post")

    print(
        json.dumps(
            {
                "status": "published",
                "post_id": post_id,
                "url": f"https://vk.ru/wall-{group['id']}_{post_id}",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--message")
    args = parser.parse_args()

    token = os.environ.get("VK_PUBLISH_TOKEN")
    if not token:
        print("VK_PUBLISH_TOKEN is not set", file=sys.stderr)
        return 1

    try:
        if args.check:
            check_token(token)
        elif args.message is not None:
            publish(token, args.message)
        else:
            parser.error("use --check or --message")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
