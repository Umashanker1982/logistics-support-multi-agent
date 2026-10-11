"""Input and output guardrails.

Input:  length limit, control-character stripping, and prompt-injection detection
        before any text reaches a model or a tool.
Output: checks on the drafted reply before it reaches the customer. A draft that
        cites unknown sources, mentions another customer's order, or claims money
        moved when it did not is rejected and replaced with a safe template.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ORDER_ID_RE = re.compile(r"\bORD-\d{4}\b", re.IGNORECASE)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Patterns that indicate an attempt to override instructions or forge authority.
INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "override_instructions": re.compile(
        r"\b(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+|your\s+)?"
        r"(previous\s+|prior\s+|above\s+|earlier\s+|system\s+)?(instructions|rules|prompts?|guidelines)\b", re.I),
    "prompt_exfiltration": re.compile(r"\b(system|developer)\s+(prompt|message|instructions)\b", re.I),
    "role_change": re.compile(
        r"\b(you are now|pretend to be|developer mode|jailbreak)\b"
        r"|\bact as (an? |the |my )?(admin|administrator|system|developer|manager|supervisor)\b", re.I),
    "authority_forgery": re.compile(
        r"\b(i am|i'm|as) (an? )?(admin|administrator|supervisor|support lead|manager)\b"
        r"|\b(pre-?approved|already approved|approval code)\b", re.I),
    "policy_bypass": re.compile(r"\b(bypass|override|skip)\b.{0,20}\b(approval|polic(y|ies)|check|rules?)\b", re.I),
    "markup_injection": re.compile(r"<\s*/?\s*(system|instructions?|assistant)\s*>", re.I),
}


@dataclass
class InputCheck:
    allowed: bool
    text: str
    reason: str = ""
    flags: list[str] = field(default_factory=list)


@dataclass
class OutputCheck:
    ok: bool
    problems: list[str] = field(default_factory=list)


def normalize_order_id(raw: str) -> str:
    return raw.upper()


def validate_input(text: str, max_chars: int) -> InputCheck:
    cleaned = _CONTROL_CHARS.sub("", text or "").strip()
    if not cleaned:
        return InputCheck(False, cleaned, "empty_message")
    if len(cleaned) > max_chars:
        return InputCheck(False, cleaned[:max_chars], "too_long", ["length_limit"])
    flags = [name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(cleaned)]
    if flags:
        return InputCheck(False, cleaned, "prompt_injection", flags)
    return InputCheck(True, cleaned)


_CITATION_RE = re.compile(r"\[([A-Z]{2,6}[- ][A-Z0-9-]+)\]")
_MONEY_CLAIM_RE = re.compile(
    r"\b(refund|claim|payout|reimbursement)\b[^.]{0,60}\b(has been|have been|was|is now|were)\s+"
    r"(issued|processed|approved|paid|sent|completed|filed)\b",
    re.I,
)


def validate_output(
    reply: str,
    *,
    allowed_sources: set[str],
    owned_order_ids: set[str],
    money_moved: bool,
    max_chars: int = 1500,
) -> OutputCheck:
    problems: list[str] = []
    if not reply.strip():
        problems.append("empty_reply")
    if len(reply) > max_chars:
        problems.append("reply_too_long")
    for cite in _CITATION_RE.findall(reply):
        if cite not in allowed_sources:
            problems.append(f"unknown_citation:{cite}")
    for oid in {normalize_order_id(m) for m in ORDER_ID_RE.findall(reply)}:
        if oid not in owned_order_ids:
            problems.append(f"foreign_order:{oid}")
    if not money_moved and _MONEY_CLAIM_RE.search(reply):
        problems.append("false_money_claim")
    return OutputCheck(not problems, problems)
