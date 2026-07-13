import hashlib
import re


POLICY_FAMILIES = (
    ("tariff", r"\b(?:tariffs?|trade war|import dut(?:y|ies))\b"),
    ("sanction", r"\bsanctions?\b"),
    ("ceasefire", r"\b(?:ceasefires?|truce|peace deal)\b"),
    ("nuclear_deal", r"\b(?:nuclear deal|nuclear agreement|nuclear talks?)\b"),
    ("military", r"\b(?:invad(?:e|es|ed|ing)|invasion|airstrikes?|strikes?|"
                 r"blockades?|military action|airspace closure|attack(?:s|ed)?)\b"),
    ("executive_order", r"\bexecutive orders?\b"),
    ("immigration", r"\b(?:immigration|deportation|border closure|asylum)\b"),
    ("government_shutdown", r"\bgovernment shutdown\b"),
    ("leadership_change", r"\b(?:leadership change|resign(?:s|ed|ation)?|"
                          r"removed from office|out as president|retire(?:s|ment)?)\b"),
    ("appointment", r"\b(?:prime minister|cabinet appointment|appointed|appointment)\b"),
    ("election", r"\b(?:elections?|nominee|nomination|referendum|ballot)\b"),
)

# Broad cross-event grouping is intentionally disabled for elections: markets for
# different candidates are related, but buying them is not the same thesis.
BROAD_TOPIC_FAMILIES = {
    "tariff", "sanction", "ceasefire", "nuclear_deal", "military",
    "immigration", "government_shutdown",
}

MULTI_ENTITY_FAMILIES = {
    "tariff", "sanction", "ceasefire", "nuclear_deal", "military",
}

ENTITY_PATTERNS = (
    ("us", r"\b(?:u\.?s\.?a?|united states|america|american|white house|"
           r"donald trump|trump administration|trump)\b"),
    ("china", r"\b(?:china|chinese|beijing|xi jinping)\b"),
    ("eu", r"\b(?:european union|e\.u\.|eu)\b"),
    ("canada", r"\b(?:canada|canadian)\b"),
    ("mexico", r"\b(?:mexico|mexican)\b"),
    ("iran", r"\b(?:iran|iranian|tehran)\b"),
    ("israel", r"\b(?:israel|israeli)\b"),
    ("russia", r"\b(?:russia|russian|moscow|putin)\b"),
    ("ukraine", r"\b(?:ukraine|ukrainian|kyiv|zelensky)\b"),
    ("taiwan", r"\b(?:taiwan|taiwanese|taipei)\b"),
    ("north_korea", r"\b(?:north korea|north korean|pyongyang)\b"),
    ("nato", r"\bnato\b"),
)

NEGATED_PROPOSITION = re.compile(
    r"\b(?:not|won't|does not|doesn't|no new|without)\b")

MONTHS = (
    r"january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)


def _clean_text(*parts) -> str:
    text = " ".join(str(part or "") for part in parts).lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def normalize_proposition(question: str) -> str:
    """Remove deadline wording while preserving the actual proposition."""
    text = _clean_text(question)
    text = re.sub(r"^will\s+", "", text)
    text = re.sub(r"\?+$", "", text)
    text = re.sub(
        rf"\b(?:by|before|on|until|through)\s+(?:the end of\s+)?"
        rf"(?:(?:{MONTHS})\b|\d{{4}}-\d{{2}}-\d{{2}}).*$",
        "", text)
    text = re.sub(rf"\b(?:{MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b", "", text)
    text = re.sub(r"\b20\d{2}\b", "", text)
    text = re.sub(r"[^a-z0-9%]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _policy_family(text: str) -> str:
    for family, pattern in POLICY_FAMILIES:
        if re.search(pattern, text):
            return family
    return ""


def _entities(text: str) -> list:
    return [name for name, pattern in ENTITY_PATTERNS if re.search(pattern, text)]


def _stance(question: str, outcome: str) -> str:
    normalized_outcome = (outcome or "").strip().upper()
    if normalized_outcome not in ("YES", "NO"):
        if normalized_outcome:
            value = re.sub(r"[^a-z0-9]+", "_", normalized_outcome.lower()).strip("_")
            return f"selected:{value}"
        return "unknown"

    proposition_is_positive = not NEGATED_PROPOSITION.search(_clean_text(question))
    outcome_supports_proposition = normalized_outcome == "YES"
    supports_topic = proposition_is_positive == outcome_supports_proposition
    return "support" if supports_topic else "oppose"


def build_market_relation(event_slug: str, event_title: str,
                          question: str, outcome: str = "") -> dict:
    text = _clean_text(event_title, event_slug, question)
    family = _policy_family(text)
    entities = _entities(text)
    proposition = normalize_proposition(question)

    series_key = ""
    if event_slug and proposition:
        digest = hashlib.sha1(proposition.encode("utf-8")).hexdigest()[:16]
        series_key = f"{event_slug}:{digest}"

    topic_key = ""
    enough_entities = (
        len(entities) >= 2 if family in MULTI_ENTITY_FAMILIES else bool(entities)
    )
    if family in BROAD_TOPIC_FAMILIES and enough_entities:
        topic_key = ":".join([family] + sorted(set(entities)))

    return {
        "policy_family": family,
        "entities": entities,
        "is_policy": bool(family),
        "topic_key": topic_key,
        "series_key": series_key,
        "proposition": proposition,
        "stance": _stance(question, outcome),
    }


def summarize_related_signals(current: dict, previous: list) -> dict:
    topic_key = current.get("topic_key", "")
    series_key = current.get("series_key", "")
    stance = current.get("stance", "unknown")

    rows = []
    seen_hashes = set()
    for row in [*previous, current]:
        tx_hash = row.get("tx_hash", "")
        if tx_hash and tx_hash in seen_hashes:
            continue
        if tx_hash:
            seen_hashes.add(tx_hash)
        same_series = bool(series_key and row.get("series_key") == series_key)
        same_topic = bool(topic_key and row.get("topic_key") == topic_key)
        if row is current or same_series or same_topic:
            rows.append(row)

    directional = [row for row in rows if row.get("stance") in ("support", "oppose")]
    aligned = [row for row in directional if row.get("stance") == stance]
    directional_notional = sum(float(row.get("notional_usdc", 0) or 0) for row in directional)
    aligned_notional = sum(float(row.get("notional_usdc", 0) or 0) for row in aligned)
    agreement = aligned_notional / directional_notional if directional_notional else 0

    markets = {row.get("condition_id") for row in aligned if row.get("condition_id")}
    wallets = {(row.get("wallet") or "").lower() for row in aligned if row.get("wallet")}
    series_markets = {
        row.get("condition_id") for row in aligned
        if series_key and row.get("series_key") == series_key and row.get("condition_id")
    }

    if len(series_markets) >= 2:
        relation_type = "same_series"
    elif len(markets) >= 2 and topic_key:
        relation_type = "same_policy_topic"
    else:
        relation_type = "none"

    return {
        "relation_type": relation_type,
        "related_market_count": len(markets),
        "related_wallet_count": len(wallets),
        "direction_agreement": agreement,
        "related_notional_usdc": aligned_notional,
        "signal_count": len(aligned),
        "is_related": len(markets) >= 2 and agreement >= 0.80,
        "is_strong": len(markets) >= 2 and len(wallets) >= 2 and agreement >= 0.80,
    }
