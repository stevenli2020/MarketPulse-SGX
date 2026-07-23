"""
Stage 1 investigation, continued: probe plausible {product_name} values
against the real gateway.mas.gov.sg/api/{product_name}/ pattern found in
the APIMG portal's own JS bundle. Even without a valid subscription key,
a 401/403 (vs a clean 404) is useful signal that the path shape/product
name is correct and only auth is missing.
"""
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

candidates = ["sora", "SORA", "domestic-interest-rates", "interest-rates",
              "exchange-rates", "statistics", "mas-data-api"]
bases = ["https://gateway.mas.gov.sg/api/", "https://dev.gateway.mas.gov.sg/api/", "https://idev.gateway.mas.gov.sg/api/"]

for base in bases:
    for c in candidates:
        url = f"{base}{c}/"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            print(f"{url} -> {resp.status_code} | Content-Type: {resp.headers.get('Content-Type', '?')} | "
                  f"len={len(resp.text)} | first 100 chars: {resp.text[:100]!r}")
        except Exception as e:
            print(f"{url} -> EXCEPTION: {e!r}")
