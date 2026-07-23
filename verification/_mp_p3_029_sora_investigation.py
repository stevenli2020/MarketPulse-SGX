"""
Stage 1 investigation script (MP-P3-029): live diagnostic probes against
the MAS datastore API and related candidate endpoints. Read-only GET
requests only - no writes, no database interaction. Prints full
diagnostics for each probe so the actual failure mode is visible, not
assumed.
"""

import sys
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def probe(label, url, params=None, headers=None, timeout=20):
    print(f"\n{'=' * 70}\nPROBE: {label}\nURL: {url}\nParams: {params}\n{'=' * 70}")
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        print(f"Status: {resp.status_code}")
        print(f"Final URL: {resp.url}")
        print(f"Content-Type: {resp.headers.get('Content-Type', '<not set>')}")
        print(f"Server header: {resp.headers.get('Server', '<not set>')}")
        print(f"Via header: {resp.headers.get('Via', '<not set>')}")
        print(f"X-Cache / Akamai-related headers: "
              f"{ {k: v for k, v in resp.headers.items() if 'cache' in k.lower() or 'akamai' in k.lower()} }")
        body = resp.text
        print(f"Body length: {len(body)} chars")
        print(f"First 400 chars of body:\n{body[:400]!r}")
        looks_json = body.strip().startswith(("{", "["))
        looks_html = body.strip().lower().startswith(("<!doctype", "<html"))
        print(f"Looks like JSON: {looks_json} | Looks like HTML: {looks_html}")
        if looks_json:
            try:
                parsed = resp.json()
                print(f"JSON parsed OK. Top-level keys: {list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}")
            except Exception as e:
                print(f"JSON parse FAILED despite looking like JSON: {e!r}")
    except Exception as e:
        print(f"REQUEST EXCEPTION: {e!r}")


def main():
    # --- Probe 1: current configured resource_id, current headers -----
    from config import MACRO_SOURCE_CONFIG
    cfg = MACRO_SOURCE_CONFIG["SORA"]
    probe(
        "Current resource_id (9a0bf149...) + current headers",
        cfg["base_url"],
        params={
            "resource_id": cfg["resource_id"],
            "between[end_of_day]": "2024-01-01,2024-01-10",
            "limit": 10,
        },
        headers=HEADERS,
    )

    # --- Probe 2: alternate resource_id documented in config.py --------
    probe(
        "Alternate resource_id (5f2b18a8...) + current headers",
        cfg["base_url"],
        params={
            "resource_id": "5f2b18a8-0883-4769-a635-879c63d3caac",
            "between[end_of_day]": "2024-01-01,2024-01-10",
            "limit": 10,
        },
        headers=HEADERS,
    )

    # --- Probe 3: current resource_id, NO custom headers (library default) ---
    probe(
        "Current resource_id + default requests headers (no custom UA)",
        cfg["base_url"],
        params={
            "resource_id": cfg["resource_id"],
            "between[end_of_day]": "2024-01-01,2024-01-10",
            "limit": 10,
        },
        headers=None,
    )

    # --- Probe 4: known-good example from MAS's own API docs (Exchange Rates) ---
    probe(
        "MAS-documented example resource_id (Exchange Rates, from official API docs)",
        "https://eservices.mas.gov.sg/api/action/datastore/search.json",
        params={"resource_id": "10eafb90-11a2-4fbd-b7a7-ac15a42d60b6", "limit": 5},
        headers=HEADERS,
    )

    # --- Probe 5: the new apimg-portal (modern API management portal) --
    probe(
        "New apimg-portal API catalog root",
        "https://eservices.mas.gov.sg/apimg-portal/api-catalog",
        headers=HEADERS,
    )

    # --- Probe 6: base eservices.mas.gov.sg domain health --------------
    probe(
        "Base eservices.mas.gov.sg domain (general health check)",
        "https://eservices.mas.gov.sg/",
        headers=HEADERS,
    )

    # --- Probe 7: data.gov.sg dataset search API for SORA/interest rate ---
    probe(
        "data.gov.sg dataset search for 'SORA'",
        "https://api-production.data.gov.sg/v2/public/api/datasets",
        params={"query": "SORA"},
        headers=HEADERS,
    )
    probe(
        "data.gov.sg dataset search for 'Domestic Interest Rates'",
        "https://api-production.data.gov.sg/v2/public/api/datasets",
        params={"query": "Domestic Interest Rates"},
        headers=HEADERS,
    )


if __name__ == "__main__":
    main()
