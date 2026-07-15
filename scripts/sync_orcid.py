#!/usr/bin/env python3
"""Synchronise public ORCID works into website-friendly JSON files."""

from __future__ import annotations

import json
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
        "id": identifiers.get("doi") or f"orcid:{work.get('put-code')}",
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

    for group in works_summary.get("group", []) or []:
        summary = choose_summary(group)
        if not summary:
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
            records.append(record)

    # A final DOI-level safeguard in case malformed grouping ever leaks duplicates.
    deduplicated: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record["id"]
        previous = deduplicated.get(key)
        if previous is None or sort_key(record) > sort_key(previous):
            deduplicated[key] = record

    records = sorted(deduplicated.values(), key=sort_key, reverse=True)
    counts = Counter(record["category"] for record in records)

    payload = {
        "orcid_id": orcid_id,
        "owner_name": config["owner_name"],
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "total": len(records),
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

    print(f"Wrote {len(records)} works to {OUTPUT_DIR / 'publications.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
