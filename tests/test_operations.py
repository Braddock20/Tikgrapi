"""The operation registry is the contract the API enforces."""
from __future__ import annotations

from app.operations import (
    BACKEND_API,
    BACKEND_HTTP,
    BACKEND_SSR,
    OPERATIONS,
    WRITE_OPERATIONS,
    describe,
    get,
)


def test_the_verified_read_operations_are_enabled():
    for name in ("me", "profile", "comments", "followers", "following"):
        assert name in OPERATIONS
        assert OPERATIONS[name].verified


def test_write_operations_are_marked_as_writes():
    for name in ("like", "unlike", "comment", "comment_delete", "follow", "unfollow"):
        assert OPERATIONS[name].write
        assert name in WRITE_OPERATIONS


def test_read_operations_are_not_writes():
    for name, spec in OPERATIONS.items():
        if name in WRITE_OPERATIONS:
            continue
        assert not spec.write


def test_each_operation_has_a_known_backend():
    for spec in OPERATIONS.values():
        assert spec.backend in (BACKEND_SSR, BACKEND_API, BACKEND_HTTP)


def test_missing_parameters_are_reported():
    spec = get("comment")
    assert spec.missing_params({}) == ["video_id", "text"]
    assert spec.missing_params({"video_id": "1", "text": "hi"}) == []


def test_describe_exposes_the_registry():
    described = describe()
    assert len(described) == len(OPERATIONS)
    assert all({"operation", "backend", "write", "verified"} <= set(d) for d in described)


def test_unverified_operations_are_not_claimed_as_verified():
    for name, spec in OPERATIONS.items():
        if name in ("search_users", "hashtag_videos"):
            assert spec.verified is False
            assert spec.note, f"{name} should explain why it is unverified"
