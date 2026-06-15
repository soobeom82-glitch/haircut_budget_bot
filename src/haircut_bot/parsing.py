from __future__ import annotations

import re
from dataclasses import dataclass


TRANSACTION_PATTERN = re.compile(
    r"^\s*(?P<label>.+?)\s+(?P<sign>[+-])?\s*(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>만|천|원)?(?:\s*[:：-]\s*.*)?$"
)
BALANCE_PATTERN = re.compile(r"잔액\s*([+-]?\d[\d,]*)원")


@dataclass(frozen=True)
class ParsedTransaction:
    label: str
    normalized_label: str
    kind: str
    amount_won: int
    amount_label: str
    delta_won: int
    raw_text: str


def parse_transaction(
    text: str,
    charge_keywords: tuple[str, ...],
    default_amount_unit: str = "man",
) -> ParsedTransaction | None:
    match = TRANSACTION_PATTERN.match(text.strip())
    if not match:
        return None

    label = " ".join(match.group("label").split())
    sign = (match.group("sign") or "").strip()
    unit = (match.group("unit") or "").strip()
    amount = _to_won(match.group("amount"), unit, default_amount_unit)
    kind = _infer_kind(label, sign, charge_keywords)
    delta = amount if kind == "charge" else -amount

    return ParsedTransaction(
        label=label,
        normalized_label=_compose_normalized_label(label),
        kind=kind,
        amount_won=amount,
        amount_label=_format_short_amount(amount),
        delta_won=delta,
        raw_text=text.strip(),
    )


def build_event_title(prefix: str, label: str, amount_won: int, balance_won: int) -> str:
    display_label = build_display_label(prefix, label)
    return f"{display_label} ({_format_short_amount(amount_won)}) 잔액 {balance_won:,}원"


def build_display_label(prefix: str, label: str) -> str:
    clean_label = " ".join(label.split())
    clean_prefix = " ".join(prefix.split())
    if not clean_prefix:
        return clean_label
    if clean_label.startswith(clean_prefix):
        return clean_label
    return f"{clean_prefix} {clean_label}"


def format_delta(delta_won: int) -> str:
    symbol = "+" if delta_won >= 0 else "-"
    return f"{symbol}{abs(delta_won):,}원"


def parse_amount_to_won(text: str, default_amount_unit: str = "man") -> int | None:
    compact = text.strip().replace(" ", "")
    match = re.fullmatch(r"(?P<amount>\d[\d,]*(?:\.\d+)?)(?P<unit>만|천|원)?", compact)
    if not match:
        return None
    unit = (match.group("unit") or "").strip()
    return _to_won(match.group("amount"), unit, default_amount_unit)


def parse_balance_from_text(text: str) -> int | None:
    match = BALANCE_PATTERN.search(text)
    if match:
        return int(match.group(1).replace(",", ""))

    for line in text.splitlines():
        if line.startswith("balance_won="):
            return int(line.split("=", 1)[1].strip())
    return None


def _infer_kind(label: str, explicit_sign: str, charge_keywords: tuple[str, ...]) -> str:
    if explicit_sign == "+":
        return "charge"
    if explicit_sign == "-":
        return "spend"

    normalized = _compose_normalized_label(label)
    for keyword in charge_keywords:
        compact_keyword = "".join(keyword.split())
        if compact_keyword and compact_keyword in normalized:
            return "charge"
    return "spend"


def _compose_normalized_label(label: str) -> str:
    return "".join(label.split())


def _to_won(amount_text: str, unit_text: str, default_amount_unit: str) -> int:
    number = float(amount_text.replace(",", ""))
    unit = unit_text or _normalize_default_unit(default_amount_unit)
    multipliers = {"won": 1, "원": 1, "thousand": 1000, "천": 1000, "man": 10000, "만": 10000}
    if unit not in multipliers:
        raise ValueError(f"Unsupported amount unit: {unit}")
    return int(number * multipliers[unit])


def _normalize_default_unit(value: str) -> str:
    text = value.strip().lower()
    aliases = {"won": "won", "원": "원", "thousand": "thousand", "천": "천", "man": "man", "만": "만"}
    return aliases.get(text, "man")


def _format_short_amount(amount_won: int) -> str:
    if amount_won % 10000 == 0:
        return f"{amount_won // 10000}만"
    if amount_won % 1000 == 0:
        return f"{amount_won // 1000}천"
    return f"{amount_won:,}원"
