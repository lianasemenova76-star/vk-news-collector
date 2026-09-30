"""Inspect or clear the published wall of tamara_vkurse only."""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

TARGET = "tamara_vkurse"
TOKEN = os.environ.get("VK_USER_TOKEN", "").strip()
MODE = os.environ.get("MODE", "inspect")
if not TOKEN:
    sys.exit("Missing secret VK_USER_TOKEN")
if MODE not in ("inspect", "delete", "publish_test"):
    sys.exit("Invalid mode")

def api(method, **params):
    body = urllib.parse.urlencode(
        dict(params, access_token=TOKEN, v="5.199")
    ).encode()
    for attempt in range(5):
        time.sleep(0.4)
        try:
            req = urllib.request.Request(
                "https://api.vk.com/method/" + method, data=body, method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.load(response)
        except Exception:
            # Do not print request data, URLs or exceptions containing secrets.
            if attempt == 4:
                raise RuntimeError("VK network request failed") from None
            time.sleep(2 ** attempt)
            continue
        error = data.get("error")
        if error:
            code = error.get("error_code")
            if code in (6, 10) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(
                f"VK API {method}: error {code}. Check token permissions and type."
            )
        if "response" not in data:
            raise RuntimeError("Unexpected VK response")
        return data["response"]

def main():
    result = api("groups.getById", group_ids=TARGET)
    groups = result.get("groups", []) if isinstance(result, dict) else result
    if len(groups) != 1:
        raise RuntimeError("Cannot identify the target community")
    group = groups[0]
    if group.get("screen_name", "").lower() != TARGET:
        raise RuntimeError("Community address mismatch: stopped")
    group_id = int(group["id"])
    if group_id <= 0:
        raise RuntimeError("Invalid community ID")
    owner_id = -group_id
    print(f"Target: https://vk.ru/{TARGET}; community ID: {group_id}", flush=True)
    if group_id != 206233289:
        raise RuntimeError("Community ID mismatch: stopped")
    if MODE == "publish_test":
        post = api("wall.post", owner_id=owner_id, from_group=1,
                   message="Тестовая публикация. Проверяем подключение к группе Тамары Борисовны.",
                   random_id=930206233289)
        if not isinstance(post, dict) or not post.get("post_id"):
            raise RuntimeError("Publication not confirmed")
        print(f"Published successfully: https://vk.ru/wall{owner_id}_{post['post_id']}", flush=True)
        return
    first = api("wall.get", owner_id=owner_id, filter="all", count=100, offset=0)
    print(f"Published wall posts: {first['count']}", flush=True)
    if MODE == "inspect":
        print("Inspection complete. Nothing deleted.", flush=True)
        return
    # Collect the complete snapshot before deleting: offsets cannot skip posts.
    posts = {}
    page = first
    offset = 0
    while True:
        items = page.get("items", [])
        for post in items:
            if int(post.get("owner_id", 0)) != owner_id:
                raise RuntimeError("Post owner mismatch: stopped")
            if post.get("post_type") not in ("post", "copy"):
                raise RuntimeError("Unexpected post type: stopped")
            posts[int(post["id"])] = post
        offset += len(items)
        if not items or offset >= int(page["count"]):
            break
        page = api("wall.get", owner_id=owner_id, filter="all", count=100, offset=offset)
    print(f"Snapshot: {len(posts)} posts to delete.", flush=True)
    deleted = 0
    for post_id in posts:
        if api("wall.delete", owner_id=owner_id, post_id=post_id) != 1:
            raise RuntimeError(f"Deletion not confirmed for post {post_id}")
        deleted += 1
        print(f"Deleted {deleted}/{len(posts)}: wall{owner_id}_{post_id}", flush=True)
    remaining = api("wall.get", owner_id=owner_id, filter="all", count=1)["count"]
    print(f"Deleted: {deleted}. Remaining published posts: {remaining}", flush=True)
    if remaining:
        raise RuntimeError("Wall is not empty. Inspect remaining posts before rerunning.")
    print("Verified: published wall is empty.", flush=True)

try:
    main()
except Exception as exc:
    print(str(exc).replace(TOKEN, "[REDACTED]"), file=sys.stderr, flush=True)
    sys.exit(1)
