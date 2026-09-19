#!/usr/bin/env python3
"""Synchronise public ORCID works into website-friendly JSON files."""

from __future__ import annotations

import json
import html
import unicodedata
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
OVERRIDES_PATH = ROOT / "data" / "overrides.json"
OUTPUT_DIR = ROOT / "docs"

TOKEN_URL = "https://orcid.org/oauth/token"
PUBLIC_API_BASE = "https://pub.orcid.org/v3.0"
ACCEPT = "application/vnd.orcid+json"
TIMEOUT = 30

TYPE_TO_CATEGORY = {
    "journal-article": "articles",
    "conference-paper": "conference-abstracts",
    "conference-output": "conference-abstracts",
    "conference-abstract": "conference-abstracts",
    "conference-presentation": "oral-communications",
    "conference-poster": "posters",
    "dissertation-thesis": "theses",
    "preprint": "articles",
    "book": "articles",
    "book-chapter": "articles",
    "public-speech": "dissemination",
    "blog-post": "dissemination",
    "magazine-article": "dissemination",
    "newspaper-article": "dissemination",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def safe_value(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip().lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi or None


def get_token(client_id: str, client_secret: str) -> str:
    response = requests.post(
        TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": "/read-public",
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("ORCID token response did not contain access_token.")
    return token


def api_get(path: str, token: str) -> dict[str, Any]:
    response = requests.get(
        f"{PUBLIC_API_BASE}{path}",
        headers={
            "Accept": ACCEPT,
            "Authorization": f"Bearer {token}",
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def metadata_score(summary: dict[str, Any]) -> tuple[int, int]:
    score = 0
    if safe_value(summary.get("title", {}).get("title")):
        score += 1
    if safe_value(summary.get("journal-title")):
        score += 1
    if summary.get("publication-date"):
        score += 1
    external_ids = summary.get("external-ids", {}).get("external-id", []) or []
    if any((item.get("external-id-type") or "").lower() == "doi" for item in external_ids):
        score += 2
    modified = int((summary.get("last-modified-date") or {}).get("value") or 0)
    return score, modified


def choose_summary(group: dict[str, Any]) -> dict[str, Any] | None:
    summaries = group.get("work-summary", []) or []
    public_summaries = [
        item for item in summaries
        if (item.get("visibility") or "PUBLIC").upper() == "PUBLIC"
    ]
    candidates = public_summaries or summaries
    if not candidates:
        return None
    return max(candidates, key=metadata_score)


def external_identifiers(work: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in work.get("external-ids", {}).get("external-id", []) or []:
        kind = (item.get("external-id-type") or "").lower().strip()
        value = safe_value(item.get("external-id-value"))
        if kind and value and kind not in result:
            result[kind] = value
    if "doi" in result:
        result["doi"] = normalize_doi(result["doi"]) or result["doi"]
    return result


def publication_date(work: dict[str, Any]) -> dict[str, int | None]:
    raw = work.get("publication-date") or {}
    def number(part: str) -> int | None:
        value = safe_value(raw.get(part))
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None
    return {"year": number("year"), "month": number("month"), "day": number("day")}


def contributors(work: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for contributor in work.get("contributors", {}).get("contributor", []) or []:
        name = safe_value(contributor.get("credit-name"))
        if name:
            names.append(name)
    return names


def resolve_url(work: dict[str, Any], identifiers: dict[str, str]) -> str | None:
    doi = identifiers.get("doi")
    if doi:
        return f"https://doi.org/{doi}"
    direct_url = safe_value(work.get("url"))
    if direct_url:
        return direct_url
    return None


def citation_value(work: dict[str, Any]) -> str | None:
    citation = work.get("citation") or {}
    return safe_value(citation.get("citation-value"))


def make_record(work: dict[str, Any]) -> dict[str, Any]:
    identifiers = external_identifiers(work)
    date = publication_date(work)
    title = safe_value(work.get("title", {}).get("title")) or "Untitled work"
    work_type = (work.get("type") or "other").lower()

    return {
        "id": f"orcid:{work.get('put-code')}",
        "orcid_put_code": work.get("put-code"),
        "type": work_type,
        "category": TYPE_TO_CATEGORY.get(work_type, "other"),
        "title": title,
        "year": date["year"],
        "month": date["month"],
        "day": date["day"],
        "venue": safe_value(work.get("journal-title")),
        "authors": contributors(work),
        "doi": identifiers.get("doi"),
        "url": resolve_url(work, identifiers),
        "citation": citation_value(work),
        "external_ids": identifiers,
    }


def apply_override(record: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    keys = [
        record.get("doi"),
        str(record.get("orcid_put_code")) if record.get("orcid_put_code") is not None else None,
    ]
    patch: dict[str, Any] = {}
    for key in keys:
        if key and isinstance(overrides.get(key), dict):
            patch.update(overrides[key])
    record.update(patch)
    return record


def sort_key(record: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        int(record.get("year") or 0),
        int(record.get("month") or 0),
        int(record.get("day") or 0),
        (record.get("title") or "").lower(),
    )


def normalized_text(value: str | None) -> str:
    """Conservative comparison key: ignore typography, not substantive words."""
    text = unicodedata.normalize("NFKD", html.unescape(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def normalized_authors(record: dict[str, Any]) -> tuple[str, ...]:
    """Ignore author ordering; require the same known names for a conference match."""
    return tuple(sorted(normalized_text(name) for name in record.get("authors", [])
                        if normalized_text(name)))


def own_doi(work: dict[str, Any]) -> str | None:
    """Never treat a proceedings-volume ('part-of') DOI as a work DOI."""
    for identifier in (work.get("external-ids") or {}).get("external-id", []) or []:
        if (identifier.get("external-id-type") or "").lower() != "doi":
            continue
        relation = (identifier.get("external-id-relationship") or "self").lower()
        if relation in ("self", "version-of"):
            return normalize_doi(safe_value(identifier.get("external-id-value")))
    return None


def same_work(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Merge equivalent representations, never unrelated conference contributions.

    A journal article may be duplicated by two ORCID sources. Other outputs
    require stricter evidence: matching type, title, venue AND contributors.
    A conference-wide DOI or conference name alone NEVER defines identity.
    """
    if a["category"] != b["category"]:
        return False
    if a["type"] != b["type"]:
        return False
    title_a, title_b = normalized_text(a.get("title")), normalized_text(b.get("title"))
    if not title_a or title_a != title_b:
        # If the same journal article DOI is present, prefer the DOI even if
        # ORCID sources have slightly different published titles.
        return (a["type"] == "journal-article" and bool(a.get("_own_doi"))
                and a.get("_own_doi") == b.get("_own_doi"))

    doi_a, doi_b = a.get("_own_doi"), b.get("_own_doi")
    if doi_a and doi_b and doi_a != doi_b:
        return False
    if a["type"] == "journal-article":
        if doi_a and doi_a == doi_b:
            return True
        # DOI missing from one source: verify title AND compatible year/venue.
        year_a, year_b = a.get("year"), b.get("year")
        if year_a and year_b and abs(int(year_a) - int(year_b)) > 1:
            return False
        venue_a, venue_b = normalized_text(a.get("venue")), normalized_text(b.get("venue"))
        return not (venue_a and venue_b and venue_a != venue_b)

    # A poster versus its abstract fails the category/type checks above.
    # For conferences, identical titles at the same event may STILL be distinct;
    # only collapse matching contributions with identical known contributors.
    # Require a title, venue, year and author list on both records.
    venue_a, venue_b = normalized_text(a.get("venue")), normalized_text(b.get("venue"))
    authors_a, authors_b = normalized_authors(a), normalized_authors(b)
    if not (venue_a and venue_b and venue_a == venue_b
            and a.get("year") and a.get("year") == b.get("year")
            and authors_a and authors_a == authors_b):
        return False
    for part in ("month", "day"):
        if a.get(part) is not None and b.get(part) is not None and a[part] != b[part]:
            return False
    # Only auto-merge conference duplicates when both have a genuine self DOI
    # OR a complete matching calendar date; no titles-only guessing.
    return bool(doi_a and doi_a == doi_b) or all(
        a.get(part) is not None and a[part] == b.get(part)
        for part in ("month", "day")
    )


def record_quality(record: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    """Prefer a fuller metadata record; use ORCID's preferred-source flag as tie-break."""
    return (
        int(bool(record.get("_own_doi"))),
        len(record.get("authors") or []),
        int(bool(record.get("venue"))),
        sum(record.get(part) is not None for part in ("year", "month", "day")),
        int(bool(record.get("citation"))),
        int(record.get("_preferred", False)),
    )


def collapse_proven_duplicates(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Use complete-link matching to avoid chaining two *different* article DOIs.

    A DOI-free title duplicate must not bridge two independently DOI-identified
    papers. Ambiguous matches are left visible for manual review.
    """
    groups: list[list[dict[str, Any]]] = []
    # Richer records arrive first so DOI-free versions join their best match.
    for record in sorted(records, key=record_quality, reverse=True):
        if record.get("keep_separate"):
            groups.append([record])
            continue
        matches = [group for group in groups
                   if all(not member.get("keep_separate") and same_work(record, member)
                          for member in group)]
        if len(matches) == 1:
            matches[0].append(record)
        else:
            # No match, or more than one credible match: never make a guess.
            groups.append([record])

    kept, merged = [], []
    for members in groups:
        winner = max(members, key=record_quality)
        # Prefer the fullest data without replacing the winner's chosen title,
        # institution/event or source-specific URL unnecessarily.
        for field in ("authors", "venue", "year", "month", "day", "citation", "doi", "url"):
            if not winner.get(field):
                for other in sorted(members, key=record_quality, reverse=True):
                    if other.get(field):
                        winner[field] = other[field]
                        break
        if winner.get("doi"):
            winner["url"] = f"https://doi.org/{normalize_doi(winner['doi'])}"
        winner["source_put_codes"] = sorted(
            {item["orcid_put_code"] for item in members if item.get("orcid_put_code") is not None}
        )
        if len(members) > 1:
            merged.append({
                "title": winner["title"],
                "category": winner["category"],
                "kept_put_code": winner["orcid_put_code"],
                "merged_put_codes": [item["orcid_put_code"] for item in members
                                     if item is not winner],
            })
        winner.pop("_own_doi", None)
        winner.pop("_preferred", None)
        winner.pop("keep_separate", None)
        kept.append(winner)
    kept.sort(key=sort_key, reverse=True)
    return kept, merged


def main() -> int:
    config = load_json(CONFIG_PATH)
    raw_overrides = load_json(OVERRIDES_PATH)
    overrides = {
        key: value
        for key, value in raw_overrides.items()
        if not key.startswith("_")
    }

    client_id = os.getenv("ORCID_CLIENT_ID")
    client_secret = os.getenv("ORCID_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "Missing ORCID_CLIENT_ID or ORCID_CLIENT_SECRET environment variables.",
            file=sys.stderr,
        )
        return 2

    token = get_token(client_id, client_secret)
    orcid_id = config["orcid_id"]
    included_types = {item.lower() for item in config.get("included_types", [])}

    works_summary = api_get(f"/{orcid_id}/works", token)
    records: list[dict[str, Any]] = []

    # ORCID's groups may contain multiple distinct public records. Never collapse
    # works by conference, DOI, title, or ORCID group. Each put code is a record.
    # Private records are not retrievable using a public API token.
    for group in works_summary.get("group", []) or []:
        for summary in group.get("work-summary", []) or []:
            if (summary.get("visibility") or "PUBLIC").upper() != "PUBLIC":
                continue
            work_type = (summary.get("type") or "").lower()
            if included_types and work_type not in included_types:
                continue
            put_code = summary.get("put-code")
            if put_code is None:
                continue
            work = api_get(f"/{orcid_id}/work/{put_code}", token)
            record = apply_override(make_record(work), overrides)
            if not record.get("hidden", False):
                record["_own_doi"] = own_doi(work)
                record["_preferred"] = summary.get("display-index") in (0, "0")
                records.append(record)

    fetched_count = len(records)
    records, merged = collapse_proven_duplicates(records)

    counts = Counter(record["category"] for record in records)

    payload = {
        "orcid_id": orcid_id,
        "owner_name": config["owner_name"],
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "total": len(records),
        "deduplication": {
            "source_records": fetched_count,
            "merged_source_records": fetched_count - len(records),
            "merged_groups": merged,
        },
        "works": records,
    }

    counts_payload = {
        "last_updated_utc": payload["last_updated_utc"],
        "total": len(records),
        "by_category": dict(sorted(counts.items())),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "publications.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (OUTPUT_DIR / "counts.json").write_text(
        json.dumps(counts_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {len(records)} distinct works from {fetched_count} public ORCID source records ")
    print(f"Merged {fetched_count - len(records)} source duplicates; details in publications.json > deduplication")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
