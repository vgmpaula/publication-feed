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
    """Ignore minor punctuation and accent differences in the *same ORCID group*."""
    text = unicodedata.normalize("NFKD", html.unescape(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def own_doi(work: dict[str, Any]) -> str | None:
    """'Part-of' identifiers describe collections, not individual contributions."""
    for identifier in (work.get("external-ids") or {}).get("external-id", []) or []:
        if (identifier.get("external-id-type") or "").lower() == "doi" and (
                identifier.get("external-id-relationship") or "self").lower() in {"self", "version-of"}:
            return normalize_doi(safe_value(identifier.get("external-id-value")))
    return None


def is_public(summary: dict[str, Any]) -> bool:
    return (summary.get("visibility") or "PUBLIC").upper() == "PUBLIC"


def preferred_score(summary: dict[str, Any]) -> tuple[int, int, int]:
    """ORCID uses the HIGHEST display index as the user's preferred source."""
    try:
        display_index = int(summary.get("display-index") or 0)
    except (TypeError, ValueError):
        display_index = 0
    return (display_index, *metadata_score(summary))


def same_contribution_within_group(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Treat two SOURCE VERSIONS as one only within their ORCID group.

    A conference-wide DOI or event name must never merge distinct titles or
    poster/paper/oral categories. There is NO matching across ORCID groups.
    """
    if a["category"] != b["category"]:
        return False
    if a.get("keep_separate") or b.get("keep_separate"):
        return False
    # ORCID groups of articles are typically different sources for one article;
    # preserve separately identified journal articles if each has a different
    # work-level DOI and a different title.
    if a["category"] == "articles":
        doi_a, doi_b = a.get("_own_doi"), b.get("_own_doi")
        if doi_a and doi_b and doi_a != doi_b:
            return False
        return True
    if normalized_text(a.get("title")) != normalized_text(b.get("title")):
        return False
    # The same talk/poster can be given at distinct events. Keep the two when
    # the *known* event names unambiguously differ.
    if a["category"] in {"posters", "oral-communications"}:
        venue_a, venue_b = normalized_text(a.get("venue")), normalized_text(b.get("venue"))
        if venue_a and venue_b and venue_a != venue_b:
            return False
    return True


CONFIG_ORCID_ID: str = ""


def select_orcid_works(works_summary: dict[str, Any], token: str,
                       included_types: set[str], overrides: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One ORCID preferred record per work, except distinct contributions in a group."""
    output: list[dict[str, Any]] = []
    group_count = 0
    public_versions = 0
    skipped_types: Counter[str] = Counter()
    excluded_by_override = 0
    grouped_versions: list[dict[str, Any]] = []

    for group in works_summary.get("group", []) or []:
        all_summaries = [s for s in (group.get("work-summary") or []) if is_public(s)]
        if not all_summaries:
            continue
        group_count += 1
        public_versions += len(all_summaries)
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for summary in all_summaries:
            work_type = (summary.get("type") or "").lower()
            if included_types and work_type not in included_types:
                skipped_types[work_type or "unknown"] += 1
                continue
            put_code = summary.get("put-code")
            if put_code is None:
                continue
            # Do not silently convert an unsuccessful request into a deletion.
            work = api_get(f"/{CONFIG_ORCID_ID}/work/{put_code}", token)
            record = apply_override(make_record(work), overrides)
            if record.get("hidden", False):
                excluded_by_override += 1
                continue
            record["_own_doi"] = own_doi(work)
            candidates.append((summary, record))

        # ORCID's preferred source decides which version represents an output.
        # All sources are examined so distinct posters/abstracts in an
        # accidentally shared group are not discarded.
        bundles: list[list[tuple[dict[str, Any], dict[str, Any]]]] = []
        for entry in candidates:
            matches = [bundle for bundle in bundles if all(
                same_contribution_within_group(entry[1], member[1]) for member in bundle
            )]
            if len(matches) == 1:
                matches[0].append(entry)
            else:
                bundles.append([entry])

        for bundle in bundles:
            selected_summary, winner = max(bundle, key=lambda entry: preferred_score(entry[0]))
            winner["source_put_codes"] = sorted({
                int(item[1]["orcid_put_code"]) for item in bundle
                if item[1].get("orcid_put_code") is not None
            })
            winner.pop("_own_doi", None)
            winner.pop("keep_separate", None)
            output.append(winner)
            if len(bundle) > 1:
                grouped_versions.append({
                    "title": winner["title"],
                    "category": winner["category"],
                    "preferred_put_code": winner["orcid_put_code"],
                    "source_put_codes": winner["source_put_codes"],
                })

    output.sort(key=sort_key, reverse=True)
    audit = {
        "orcid_public_groups": group_count,
        "orcid_public_source_versions": public_versions,
        "displayed_outputs": len(output),
        "grouped_source_versions": grouped_versions,
        "skipped_work_types": dict(sorted(skipped_types.items())),
        "hidden_by_overrides": excluded_by_override,
        "note": "ORCID groups can contain several source versions, or occasionally distinct poster/abstract contributions. The website rebuilds from ORCID at every successful sync.",
    }
    return output, audit


def main() -> int:
    global CONFIG_ORCID_ID
    config = load_json(CONFIG_PATH)
    overrides_data = load_json(OVERRIDES_PATH)
    overrides = {k: v for k, v in overrides_data.items() if not k.startswith("_")}
    client_id, client_secret = os.getenv("ORCID_CLIENT_ID"), os.getenv("ORCID_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("Missing ORCID_CLIENT_ID or ORCID_CLIENT_SECRET", file=sys.stderr)
        return 2

    CONFIG_ORCID_ID = config["orcid_id"]
    token = get_token(client_id, client_secret)
    groups = api_get(f"/{CONFIG_ORCID_ID}/works", token)
    if not isinstance(groups.get("group"), list):
        raise RuntimeError("ORCID did not return a valid groups list; leaving existing files unchanged.")
    included_types = {item.lower() for item in config.get("included_types", [])}
    records, audit = select_orcid_works(groups, token, included_types, overrides)
    # Guard against a transient empty API response wiping a populated website.
    old_path = OUTPUT_DIR / "publications.json"
    if not records and old_path.exists():
        previous = load_json(old_path)
        if previous.get("works"):
            raise RuntimeError("ORCID returned no eligible works; refusing to overwrite a populated feed.")
    counts = Counter(record["category"] for record in records)
    updated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "orcid_id": CONFIG_ORCID_ID,
        "owner_name": config["owner_name"],
        "last_updated_utc": updated_at,
        "total": len(records),
        "orcid_sync": audit,
        "works": records,
    }
    count_payload = {
        "last_updated_utc": updated_at,
        "total": len(records),
        "by_category": dict(sorted(counts.items())),
    }
    # Both JSON files are generated completely in memory before replacement.
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, contents in (
        (OUTPUT_DIR / "publications.json", payload),
        (OUTPUT_DIR / "counts.json", count_payload),
    ):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(contents, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    print(f"ORCID groups: {audit['orcid_public_groups']}; underlying source versions: {audit['orcid_public_source_versions']}")
    print(f"Displayed outputs: {len(records)}; skipped types: {audit['skipped_work_types']}")
    print("Rebuilt publications.json and counts.json from current ORCID data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
