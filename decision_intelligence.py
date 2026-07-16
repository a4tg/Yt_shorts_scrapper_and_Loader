from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable


KIND_PATTERNS = {
    "decision": re.compile(r"\b(решил[аи]?|решено|решили|договорились|утвердили|выбираем|фиксируем|decision|decided|agreed|approved)\b", re.I),
    "commitment": re.compile(r"\b(сделаю|беру\s+на\s+себя|обязуюсь|подготовлю|пришлю|отправлю|проверю|will\s+(do|send|prepare|check)|i(?:'ll|\s+will))\b", re.I),
    "action": re.compile(r"\b(todo|нужно|надо|необходимо|задача|сделать|исправить|добавить|обновить|подготовить|проверить|action\s+item)\b", re.I),
    "risk": re.compile(r"\b(риск|блокер|блокирует|проблема|опасность|не\s+успеем|задержка|risk|blocker|blocked|delay)\b", re.I),
    "question": re.compile(r"\?|\b(вопрос|уточнить|непонятно|кто\s+решает|question|clarify)\b", re.I),
}


def aware_datetime(value: datetime | None) -> datetime | None:
    """Return a UTC-aware datetime, including values read back as naive by SQLite."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def classify_text(text: str) -> list[tuple[str, float]]:
    clean = " ".join((text or "").split())
    result: list[tuple[str, float]] = []
    for kind, pattern in KIND_PATTERNS.items():
        if pattern.search(clean):
            confidence = .9 if kind in {"decision", "risk"} else .82
            result.append((kind, confidence))
    if len(result) > 3:
        result = result[:3]
    return result


def extract_due_at(text: str, reference: datetime | None = None) -> datetime | None:
    reference = aware_datetime(reference) or datetime.now(timezone.utc)
    lowered = text.casefold()
    if "послезавтра" in lowered:
        return (reference + timedelta(days=2)).replace(hour=18, minute=0, second=0, microsecond=0)
    if "завтра" in lowered or "tomorrow" in lowered:
        return (reference + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    if "сегодня" in lowered or "today" in lowered:
        return reference.replace(hour=18, minute=0, second=0, microsecond=0)
    iso = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
    local = re.search(r"\b(\d{1,2})[./](\d{1,2})[./](20\d{2})\b", text)
    try:
        if iso:
            return datetime(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)), 18, tzinfo=timezone.utc)
        if local:
            return datetime(int(local.group(3)), int(local.group(2)), int(local.group(1)), 18, tzinfo=timezone.utc)
    except ValueError:
        return None
    return None


def insight_title(text: str, kind: str) -> str:
    clean = " ".join((text or "").split()).strip()
    sentence = re.split(r"(?<=[.!?])\s+", clean, maxsplit=1)[0]
    prefixes = {"decision": "Решение", "commitment": "Обязательство", "action": "Действие", "risk": "Риск", "question": "Открытый вопрос"}
    if len(sentence) > 210:
        sentence = sentence[:207].rstrip() + "…"
    return sentence or prefixes.get(kind, "Сигнал")


def fingerprint(project_id: str, kind: str, source_type: str, source_id: str | None, title: str) -> str:
    normalized = re.sub(r"\s+", " ", title.casefold()).strip()
    raw = f"{project_id}|{kind}|{source_type}|{source_id or ''}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def priority_for(kind: str, text: str, due_at: datetime | None, now: datetime | None = None) -> str:
    now = aware_datetime(now) or datetime.now(timezone.utc)
    due_at = aware_datetime(due_at)
    lowered = text.casefold()
    if any(word in lowered for word in ("срочно", "критично", "urgent", "critical")):
        return "urgent"
    if kind == "risk" or (due_at and due_at < now):
        return "high"
    if due_at and due_at <= now + timedelta(days=2):
        return "high"
    return "normal" if kind in {"action", "commitment", "question"} else "low"


def impact_score(kind: str, priority: str, due_at: datetime | None = None, link_weight: float = 0, now: datetime | None = None) -> float:
    now = aware_datetime(now) or datetime.now(timezone.utc)
    due_at = aware_datetime(due_at)
    value = {"decision": 2.5, "commitment": 3, "action": 3, "risk": 5, "question": 2}.get(kind, 1)
    value += {"low": 0, "normal": 1, "high": 3, "urgent": 5}.get(priority, 0)
    if due_at and due_at < now:
        value += 4
    elif due_at and due_at <= now + timedelta(days=2):
        value += 2
    return round(min(20, value + min(5, link_weight)), 2)


def parse_ai_intelligence(raw: str) -> dict[str, object]:
    value = raw.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("AI response must be an object")
    return parsed


def deterministic_briefing(project_name: str, insights: Iterable[dict[str, object]], stats: dict[str, int]) -> dict[str, object]:
    items = list(insights)
    decisions = [item for item in items if item.get("kind") == "decision"]
    risks = [item for item in items if item.get("kind") == "risk"]
    actions = [item for item in items if item.get("kind") in {"action", "commitment"}]
    summary = (
        f"В проекте «{project_name}» сейчас {stats.get('open_insights', 0)} активных сигналов, "
        f"{stats.get('open_reviews', 0)} незакрытых замечаний и {stats.get('overdue', 0)} просроченных материалов. "
        f"Зафиксировано решений: {len(decisions)}; действий и обязательств: {len(actions)}."
    )
    def compact(values, limit=5):
        return [{"id": item.get("id"), "title": item.get("title"), "priority": item.get("priority"), "impact_score": item.get("impact_score")} for item in values[:limit]]
    return {"summary": summary, "highlights": compact(decisions), "risks": compact(risks), "next_actions": compact(actions)}
