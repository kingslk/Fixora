from __future__ import annotations

import pytest
from pydantic import ValidationError

from fixora.agent.runtime import build_model_settings
from fixora.config import normalize_model_base_url
from fixora.http.schemas import FeedbackInput, ModelSettingsInput, TaskCreate


def test_model_parameters_cannot_override_agent_contract() -> None:
    with pytest.raises(ValidationError):
        ModelSettingsInput(
            api_url="https://api.example.com/v1",
            api_key="key",
            api_mode="responses",
            model="gpt-test",
            parameters={"tools": []},
        )


def test_model_settings_map_both_api_parameter_names() -> None:
    settings = build_model_settings(
        {
            "reasoning_effort": "high",
            "parameters": {"max_output_tokens": 4096, "temperature": 0.2, "custom": "x"},
        }
    )
    assert settings.max_tokens == 4096
    assert settings.temperature == 0.2
    assert settings.reasoning and settings.reasoning.effort == "high"
    assert settings.extra_body == {"custom": "x"}


def test_task_create_allows_screenshot_only() -> None:
    task = TaskCreate(
        repository_id=1,
        image_data_url="data:image/png;base64,abc",
        image_name="bug.png",
    )
    assert task.description == ""


def test_task_create_requires_description_without_screenshot() -> None:
    with pytest.raises(ValidationError, match="问题描述"):
        TaskCreate(repository_id=1)


def test_feedback_requires_reason_for_incorrect_fix() -> None:
    with pytest.raises(ValidationError, match="具体原因"):
        FeedbackInput(rating="incorrect")
    assert FeedbackInput(rating="perfect").reason == ""


def test_model_base_url_accepts_gateway_and_full_endpoint_forms() -> None:
    assert normalize_model_base_url("https://gateway.example/deepseek") == "https://gateway.example/deepseek/"
    assert normalize_model_base_url("https://gateway.example/deepseek/v1/") == "https://gateway.example/deepseek/v1/"
    assert normalize_model_base_url("https://gateway.example/deepseek/chat/completions") == "https://gateway.example/deepseek/"
    assert normalize_model_base_url("https://gateway.example/deepseek/responses") == "https://gateway.example/deepseek/"
