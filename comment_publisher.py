#!/usr/bin/env python3
"""Publish exactly one explicitly approved VK comment from the Mой Тутаев community.

The owner is always resolved from GROUP_DOMAIN in publisher.py (vtutaeve).
No comments are published by validation or by unconfirmed requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from publisher import GROUP_DOMAIN, api_call, resolve_group


def is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def validate_request(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Request must be a JSON object")
    if payload.get("confirmed") is not True:
        raise ValueError("Comment has not been explicitly confirmed")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
        raise ValueError("request_id must be a unique 8-64 character identifier")
    post_id = payload.get("post_id")
    if not is_positive_int(post_id):
        raise ValueError("post_id must be a positive integer")
    reply_to_comment = payload.get("reply_to_comment")
    if reply_to_comment is not None and not is_positive_int(reply_to_comment):
        raise ValueError("reply_to_comment must be null or a positive integer")
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip() or len(message.strip()) > 4000:
        raise ValueError("message must contain 1-4000 characters")
    if "owner_id" in payload or "from_group" in payload:
        raise ValueError("Owner and author cannot be overridden in a request")
    return {
        "request_id": request_id,
        "post_id": post_id,
        "reply_to_comment": reply_to_comment,
        "message": message.strip(),
    }


def request_guid(request_id: str) -> str:
    # VK uses guid to avoid creating duplicate comments if a workflow retries.
    digest = hashlib.sha256(("vtutaeve-comment:" + request_id).encode("utf-8")).digest()
    return str(int.from_bytes(digest[:8], "big") & ((1 << 63) - 1) or 1)


def publish_comment(request: dict, token: str) -> dict:
    group = resolve_group(token)
    group_id = int(group["id"])
    if group_id <= 0 or GROUP_DOMAIN != "vtutaeve":
        raise RuntimeError("Unexpected VK community")

    # Read-only preflight: never post to a missing or foreign wall entry.
    post_ref = f"-{group_id}_{request['post_id']}"
    response = api_call("wall.getById", token, posts=post_ref)
    posts = response if isinstance(response, list) else (response.get("items", []) if isinstance(response, dict) else [])
    if not posts or int(posts[0].get("owner_id", 0)) != -group_id:
        raise RuntimeError("Post not found on the Мой Тутаев wall")

    params = {
        "owner_id": -group_id,
        "post_id": request["post_id"],
        "from_group": 1,
        "message": request["message"],
        "guid": request_guid(request["request_id"]),
    }
    if request["reply_to_comment"] is not None:
        params["reply_to_comment"] = request["reply_to_comment"]

    created = api_call("wall.createComment", token, **params)
    comment_id = created.get("comment_id") if isinstance(created, dict) else None
    if not is_positive_int(comment_id):
        raise RuntimeError("VK did not confirm comment creation")
    return {
        "status": "published",
        "community": GROUP_DOMAIN,
        "post_id": request["post_id"],
        "comment_id": comment_id,
        "reply_to_comment": request["reply_to_comment"],
        "url": f"https://vk.ru/wall-{group_id}_{request['post_id']}?reply={comment_id}",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, default=Path("comment-publish-request.json"))
    parser.add_argument("--validate", action="store_true", help="Validate without making any VK API calls")
    parser.add_argument("--send", action="store_true", help="Publish only after explicit confirmation")
    args = parser.parse_args()
    if args.validate == args.send:
        parser.error("specify exactly one of --validate or --send")

    try:
        document = json.loads(args.request.read_text(encoding="utf-8"))
        if document.get("confirmed") is not True:
            if args.validate:
                print(json.dumps({"status": "unconfirmed", "published": False}, ensure_ascii=False))
                return 0
            raise ValueError("Comment is not approved")
        req = validate_request(document)
        if args.validate:
            print(json.dumps({
                "status": "validated", "published": False,
                "request_id": req["request_id"],
                "post_id": req["post_id"],
                "reply_to_comment": req["reply_to_comment"],
            }, ensure_ascii=False))
            return 0
        if os.environ.get("CONFIRM_COMMENT") != "YES":
            raise RuntimeError("Posting requires CONFIRM_COMMENT=YES")
        token = os.environ.get("VK_USER_TOKEN") or os.environ.get("VK_PUBLISH_TOKEN")
        if not token:
            raise RuntimeError("No existing VK token is available in workflow secrets")
        print(json.dumps(publish_comment(req, token), ensure_ascii=False))
        return 0
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError, KeyError) as exc:
        print(f"VK comment workflow failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
