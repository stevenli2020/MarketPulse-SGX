"""Stage 1 investigation, continued: look for the exact auth header/param
convention and any product-name hints near 'subscriptionKey' in the bundle."""
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
JS_URL = "https://eservices.mas.gov.sg/apimg-portal/static/js/main.93dab40a.js"

resp = requests.get(JS_URL, headers=HEADERS, timeout=30)
text = resp.text

for needle in ["subscriptionKey", "x-api-key", "apikey", "api-key", "gateway.mas.gov.sg/api/"]:
    idx = text.lower().find(needle.lower())
    if idx != -1:
        print(f"--- context around {needle!r} (first occurrence) ---")
        print(text[max(0, idx-150):idx+250])
        print()

# Also try to find product name examples used alongside the gateway URL pattern
import re
for m in list(set(re.findall(r'gateway\.mas\.gov\.sg/api/[^\s"\'\\{}]*', text)))[:10]:
    print("product-name-like match:", m)
