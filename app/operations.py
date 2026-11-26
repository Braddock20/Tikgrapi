"""Operation registry.

Every automatable action is declared here with the backend that can actually
perform it, whether it changes account state, and whether it has been
verified against the live site. The API refuses operations that are not
registered, and `verified=False` operations are rejected until someone has
confirmed them against a real account.

Backends
--------
``ssr``    server-rendered profile page (read-only, no signature needed)
``api``    signed JSON API through the browser session
``http``   plain JSON API over HTTP (no signature required for these paths)
"""
from __future__ import annotations

from dataclasses import dataclass, field

BACKEND_SSR = "ssr"
BACKEND_API = "api"
BACKEND_HTTP = "http"
BACKEND_UPLOAD = "upload"


@dataclass(frozen=True)
class OperationSpec:
    name: str
    summary: str
    backend: str
    write: bool = False
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    verified: bool = False
    note: str = ""

    def missing_params(self, params: dict) -> list[str]:
        return [p for p in self.required if params.get(p) in (None, "")]


OPERATIONS: dict[str, OperationSpec] = {
    # ---------------- read-only ----------------
    "me": OperationSpec(
        name="me",
        summary="Profile of the account the session belongs to",
        backend=BACKEND_SSR,
        verified=True,
        note="Read from the page's server-rendered data blob.",
    ),
    "profile": OperationSpec(
        name="profile",
        summary="Public profile and stats of a user by username",
        backend=BACKEND_SSR,
        required=("username",),
        verified=True,
        note="The signed user/detail endpoint returns an empty body from "
             "datacenter IPs, so this falls back to the rendered page.",
    ),
    "user_posts": OperationSpec(
        name="user_posts",
        summary="Videos published by a user",
        backend=BACKEND_API,
        required=("sec_uid",),
        optional=("count", "cursor"),
        verified=True,
    ),
    "comments": OperationSpec(
        name="comments",
        summary="Comments on a video",
        backend=BACKEND_API,
        required=("video_id",),
        optional=("count", "cursor"),
        verified=True,
    ),
    "followers": OperationSpec(
        name="followers",
        summary="List of a user's followers",
        backend=BACKEND_HTTP,
        required=("sec_uid", "user_id"),
        optional=("count", "cursor"),
        verified=True,
    ),
    "following": OperationSpec(
        name="following",
        summary="List of accounts a user follows",
        backend=BACKEND_HTTP,
        required=("sec_uid", "user_id"),
        optional=("count", "cursor"),
        verified=True,
    ),
    "explore_feed": OperationSpec(
        name="explore_feed",
        summary="Explore / For-You feed items",
        backend=BACKEND_API,
        optional=("count",),
        verified=True,
    ),
    "upload": OperationSpec(
        name="upload",
        summary="Post a video to the account",
        backend=BACKEND_UPLOAD,
        write=True,
        required=("video_path",),
        optional=("caption", "visibility", "allow_comment", "allow_duet",
                  "allow_stretch", "schedule_time"),
        verified=True,
        note="Publishes through TikTok's creator pipeline — verified live.",
    ),
    "search_users": OperationSpec(
        name="search_users",
        summary="Search for users by keyword",
        backend=BACKEND_API,
        required=("keyword",),
        optional=("count", "cursor"),
        verified=False,
        note="Needs extra session state; verify before enabling.",
    ),
    "hashtag_videos": OperationSpec(
        name="hashtag_videos",
        summary="Videos under a hashtag",
        backend=BACKEND_API,
        required=("tag",),
        optional=("count", "cursor"),
        verified=False,
        note="Needs extra session state; verify before enabling.",
    ),
    # ---------------- write ----------------
    "like": OperationSpec(
        name="like",
        summary="Like a video",
        backend=BACKEND_API,
        write=True,
        required=("video_id",),
    ),
    "unlike": OperationSpec(
        name="unlike",
        summary="Remove a like from a video",
        backend=BACKEND_API,
        write=True,
        required=("video_id",),
    ),
    "comment": OperationSpec(
        name="comment",
        summary="Publish a comment on a video",
        backend=BACKEND_API,
        write=True,
        required=("video_id", "text"),
    ),
    "comment_delete": OperationSpec(
        name="comment_delete",
        summary="Delete one of the account's own comments",
        backend=BACKEND_API,
        write=True,
        required=("video_id", "comment_id"),
    ),
    "delete_video": OperationSpec(
        name="delete_video",
        summary="Delete one of the account's own videos",
        backend="upload",
        write=True,
        required=("aweme_id",),
    ),
    "visibility": OperationSpec(
        name="visibility",
        summary="Change a video's visibility (0 public, 1 friends, 2 private)",
        backend="upload",
        write=True,
        required=("aweme_id",),
        optional=("visibility",),
    ),
    "follow": OperationSpec(
        name="follow",
        summary="Follow a user",
        backend=BACKEND_API,
        write=True,
        required=("sec_uid", "user_id"),
    ),
    "unfollow": OperationSpec(
        name="unfollow",
        summary="Unfollow a user",
        backend=BACKEND_API,
        write=True,
        required=("sec_uid", "user_id"),
    ),
}

WRITE_OPERATIONS = frozenset(k for k, v in OPERATIONS.items() if v.write)


def get(name: str) -> OperationSpec | None:
    return OPERATIONS.get(name)


def verified_operations() -> dict[str, OperationSpec]:
    return {k: v for k, v in OPERATIONS.items() if v.verified}


def describe() -> list[dict]:
    return [
        {
            "operation": v.name,
            "summary": v.summary,
            "backend": v.backend,
            "write": v.write,
            "verified": v.verified,
            "required": list(v.required),
            "optional": list(v.optional),
            "note": v.note,
        }
        for v in OPERATIONS.values()
    ]
