import base64
import zlib
import re
import urllib.request
import os

def kroki_encode(text):
    compressed = zlib.compress(text.encode('utf-8'), 9)
    return base64.urlsafe_b64encode(compressed).decode('ascii')

with open('DESIGN_DOCUMENT.md', 'r') as f:
    content = f.read()

matches = re.finditer(r'```mermaid\n(.*?)\n```', content, re.DOTALL)
names = ["erd", "dfd", "sequence", "state"]

os.makedirs("diagrams", exist_ok=True)

for i, match in enumerate(matches):
    mermaid_code = match.group(1).strip()
    encoded = kroki_encode(mermaid_code)
    url = f"https://kroki.io/mermaid/png/{encoded}"
    out_file = f"diagrams/diagram_{names[i]}.png"
    print(f"Downloading {out_file}...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response, open(out_file, 'wb') as out_f:
            out_f.write(response.read())
        print(f"✅ Saved {out_file}")
    except Exception as e:
        print(f"❌ Failed {out_file}: {e}")
