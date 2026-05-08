import base64
import zlib
import re

def kroki_encode(text):
    compressed = zlib.compress(text.encode('utf-8'), 9)
    return base64.urlsafe_b64encode(compressed).decode('ascii')

with open('DESIGN_DOCUMENT.md', 'r') as f:
    content = f.read()

matches = list(re.finditer(r'```mermaid\n(.*?)\n```', content, re.DOTALL))
names = ["erd", "dfd", "sequence", "state"]

for i, match in enumerate(matches):
    mermaid_code = match.group(1).strip()
    encoded = kroki_encode(mermaid_code)
    url = f"https://kroki.io/mermaid/svg/{encoded}"
    print(f"{names[i]}_URL = '{url}'")

