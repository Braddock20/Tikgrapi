"""Schema validation for job creation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import JobCreate


def test_a_known_operation_is_accepted():
    job = JobCreate(operation="me")
    assert job.operation == "me"
    assert job.params == {}


def test_an_unknown_operation_is_rejected_by_the_validator():
    with pytest.raises(ValidationError):
        JobCreate(operation="not_a_real_operation")


def test_params_default_to_an_empty_mapping():
    assert JobCreate(operation="profile").params == {}


def test_an_idempotency_key_is_length_limited():
    with pytest.raises(ValidationError):
        JobCreate(operation="me", idempotency_key="x" * 200)
