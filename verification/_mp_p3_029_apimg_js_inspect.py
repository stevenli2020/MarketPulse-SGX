"""
Stage 1 investigation, continued: fetch the apimg-portal's JS bundle
directly and search it for embedded API base URLs / endpoint patterns.
Read-only. This is a standard, safe technique for discovering an SPA's
backend API shape when the portal itself requires JavaScript to render.
"""
import re
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

JS_URL = "https://eservices.mas.gov.sg/apimg-portal/static/js/main.93dab40a.js"

resp = requests.get(JS_URL, headers=HEADERS, timeout=30)
print(f"Status: {resp.status_code}, length: {len(resp.text)} chars\n")

text = resp.text

# Look for anything that looks like an API base URL or path
patterns = [
    r'https?://[a-zA-Z0-9.\-]*mas\.gov\.sg[^\s"\'\\]*',
    r'"/apimg[^"]*"',
    r'apiBaseUrl["\']?\s*[:=]\s*["\'][^"\']+["\']',
    r'subscription[-_]?key',
    r'Ocp-Apim-Subscription-Key',
    r'swagger|openapi',
]
for pat in patterns:
    matches = sorted(set(re.findall(pat, text, re.IGNORECASE)))[:15]
    print(f"Pattern {pat!r}: {len(matches)} unique match(es)")
    for m in matches:
        print(f"    {m}")
    print()
