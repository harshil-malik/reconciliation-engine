#!/usr/bin/env bash
# Downloads the three webfonts Munim AI's landing page uses and stores them
# next to it, so the page renders with no outbound request to Google.
#
# Run from the folder containing index.html:  bash fetch-fonts.sh
set -euo pipefail

DEST="fonts"
CSS_URL='https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,500&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap'
# a modern desktop UA makes Google serve woff2 rather than older formats
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'

mkdir -p "$DEST"
echo "Fetching the stylesheet…"
curl -fsS -A "$UA" "$CSS_URL" -o "$DEST/google.css"

echo "Downloading the latin subsets…"
python3 - "$DEST" <<'PY'
import re, sys, os, urllib.request
dest = sys.argv[1]
css  = open(os.path.join(dest, "google.css")).read()
ua   = {'User-Agent': 'Mozilla/5.0'}
out, seen = [], set()

for block in re.findall(r'@font-face\s*\{(.*?)\}', css, re.S):
    # keep only latin and latin-ext; the cyrillic/greek/vietnamese subsets are dead weight here
    ur = re.search(r'unicode-range:\s*([^;]+);', block)
    if not ur or 'U+0000-00FF' not in ur.group(1):
        continue
    fam = re.search(r"font-family:\s*'([^']+)'", block).group(1)
    wgt = re.search(r'font-weight:\s*([^;]+);', block).group(1).strip()
    sty = (re.search(r'font-style:\s*([^;]+);', block) or [None,'normal'])[1].strip()
    url = re.search(r'url\((https://[^)]+)\)', block).group(1)

    name = f"{fam.replace(' ','')}-{wgt.replace(' ','_')}.woff2"
    if name in seen:
        continue
    seen.add(name)
    urllib.request.urlretrieve(url, os.path.join(dest, name))
    print(f"  {name}")
    out.append(f"""@font-face{{
  font-family:'{fam}';
  font-style:{sty};
  font-weight:{wgt};
  font-display:swap;
  src:url('{dest}/{name}') format('woff2');
  unicode-range:{ur.group(1).strip()};
}}""")

open(os.path.join(dest, "local-fonts.css"), "w").write("\n".join(out) + "\n")
print(f"\nWrote {len(out)} @font-face rules to {dest}/local-fonts.css")
PY

rm -f "$DEST/google.css"
echo
echo "Done. Total size:"
du -ch "$DEST"/*.woff2 | tail -1
echo
echo "Now tell Claude it's finished and it will wire the CSS into index.html."
