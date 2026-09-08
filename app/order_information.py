"""Read legacy and multipart manual-order information without losing content.

This module only projects data. Authorization and writes belong to Database;
file IDs must never be included in list labels, logs or callback payloads.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def decode(value: Any) -> dict:
    if isinstance(value, Mapping):
        return dict(value)
    if not value:
        return {}
    result = json.loads(str(value))
    if not isinstance(result, dict):
        raise ValueError("invalid order information")
    return result


def messages(info: Mapping[str, Any]) -> list[dict]:
    if isinstance(info.get("messages"), list):
        return [dict(item) for item in info["messages"] if isinstance(item, Mapping)]
    return [dict(info)] if info.get("text") or info.get("file_id") else []


def attachments(info: Mapping[str, Any]) -> list[dict]:
    return [item for item in messages(info)
            if str(item.get("file_id") or "").strip() and item.get("file_kind") in {"photo", "document"}]


def text(info: Mapping[str, Any]) -> str:
    return "\n\n".join(str(item["text"]) for item in messages(info) if item.get("text"))


def aggregate(info: dict, items: list[dict]) -> dict:
    result = {**info, "messages": items}
    result["text"] = text(result)
    files = attachments(result)
    # Preserve the first file for older read-only tools; new readers use all
    # messages. A text-only submission has no attachment kind at all.
    result["file_id"] = files[0]["file_id"] if files else None
    result["file_kind"] = files[0]["file_kind"] if files else None
    return result
