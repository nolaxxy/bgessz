#!/usr/bin/env python3
"""
Baugesuch-Fetcher für Kanton Schwyz
====================================
Holt Baugesuche via REST-API von amtsblatt.sz.ch,
parst XML → JSON und schreibt data/baugesuche.json.

Die Website (index.html) ist komplett getrennt und liest
diese JSON-Datei beim Laden. Dieses Script wird nur vom
Cron-Job aufgerufen und verändert NIE die Website selbst.

API:
  GET https://amtsblatt.sz.ch/api/v1/publications
    ?tenant=kabsz&subRubrics=BA-SZ05
    &publicationStates=PUBLISHED
    &includeContent=true
    &pageRequest.page={n}&pageRequest.size=100
"""

import re, json, html, os, sys, argparse, urllib.request, urllib.error
from datetime import datetime, timezone

# ─── Config ───
API_BASE = "https://amtsblatt.sz.ch/api/v1/publications"
API_PARAMS = {
    "tenant": "kabsz",
    "subRubrics": "BA-SZ05",
    "publicationStates": "PUBLISHED",
    "includeContent": "true",
    "pageRequest.size": "100",
}
OUTPUT_PATH = "data/baugesuche.json"
MAX_PAGES = 5  # Safety limit: max 500 Baugesuche


def lv95_to_wgs84(east, north):
    y = (east - 2_600_000) / 1_000_000
    x = (north - 1_200_000) / 1_000_000
    lon = 2.6779094 + 4.728982*y + 0.791484*y*x + 0.1306*y*x*x - 0.0436*y*y*y
    lat = 16.9023892 + 3.238272*x - 0.270978*y*y - 0.002528*x*x - 0.0447*y*y*x - 0.014*x*x*x
    return round(lat * 100/36, 6), round(lon * 100/36, 6)


def xtag(xml, tag):
    m = re.search(f'<{tag}[^>]*>(.*?)</{tag}>', xml, re.DOTALL)
    return m.group(1).strip() if m else None

def xall(xml, tag):
    return [m.strip() for m in re.findall(f'<{tag}[^>]*>(.*?)</{tag}>', xml, re.DOTALL)]

def clean(text):
    if not text: return ""
    text = html.unescape(text)
    for a, b in [('&amp;','&'),('&lt;','<'),('&gt;','>'),('&auml;','ä'),('&ouml;','ö'),
                  ('&uuml;','ü'),('&Auml;','Ä'),('&Ouml;','Ö'),('&Uuml;','Ü'),('&nbsp;',' ')]:
        text = text.replace(a, b)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', text)).strip()

def parse_coords(text):
    if not text: return None, None
    m = re.search(r'Koordinaten\s*(\d[\d\s\u00a0\']*\d)\s*[\/,]\s*(\d[\d\s\u00a0\']*\d)', text, re.I)
    if m:
        e = int(re.sub(r'[\s\u00a0\']', '', m.group(1)))
        n = int(re.sub(r'[\s\u00a0\']', '', m.group(2)))
        if 2_000_000 < e < 3_000_000 and 1_000_000 < n < 1_400_000:
            return e, n
    return None, None

def detect_type(d):
    d = d.lower()
    if 'abbruch' in d and 'neubau' in d: return 'Abbruch/Neubau'
    if 'neubau' in d: return 'Neubau'
    if 'umbau' in d: return 'Umbau'
    if 'abbruch' in d: return 'Abbruch'
    if 'anbau' in d or 'erweiterung' in d: return 'Anbau/Erweiterung'
    if 'sanierung' in d or 'renovation' in d: return 'Sanierung'
    if 'photovoltaik' in d or 'solaranlage' in d or 'wärmepumpe' in d: return 'Energetisch'
    return 'Baugesuch'


def parse_publication(block):
    pub_number = xtag(block, 'publicationNumber')
    if not pub_number or not pub_number.startswith('BA-SZ05'): return None

    title_block = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
    title_de = xtag(title_block.group(1), 'de') if title_block else None

    reg = xtag(block, 'registrationOffice')
    loc = xtag(block, 'localization')
    mun = xtag(loc, 'municipalityId') if loc else None

    bauherr, bauherr_adr, beschreibung = None, None, None
    for sec in xall(block, 'secondary'):
        if 'buildingContractor' in sec:
            name = xtag(sec, 'officialName')
            first = xtag(sec, 'firstName')
            if name: bauherr = f"{first} {name}".strip() if first else name.strip()
            s, z, t = xtag(sec,'street'), xtag(sec,'swissZipCode'), xtag(sec,'town')
            if s and z and t: bauherr_adr = f"{s}, {z} {t}"
        if 'projectDescription' in sec:
            rt = xtag(sec, 'valueRichText')
            if rt: beschreibung = clean(xtag(rt, 'de'))

    pv = None
    if beschreibung:
        m = re.search(r'Projektverfasser(?:in)?:\s*(.+?)(?:\.\s*Bauobjekt|,\s*\d{4})', beschreibung, re.I)
        if m: pv = m.group(1).strip()

    bo = None
    if beschreibung:
        m = re.search(r'Bauobjekt:\s*(.+?)(?:,\s*(?:GB|KTN|Koordinaten|Parzelle))', beschreibung, re.I)
        if m: bo = m.group(1).strip()

    east, north = parse_coords(beschreibung)
    lat, lon = lv95_to_wgs84(east, north) if east else (None, None)

    dl = xtag(block, 'deadline')
    df = xtag(dl, 'dateFrom')[:10] if dl and xtag(dl, 'dateFrom') else None
    dt = xtag(dl, 'dateTo')[:10] if dl and xtag(dl, 'dateTo') else None

    ct = re.search(r'<contact>\s*<de>(.*?)</de>\s*</contact>', block, re.DOTALL)

    return {
        'id': xtag(block, 'id'),
        'title': title_de,
        'pubNumber': pub_number,
        'pubDate': (xtag(block,'publicationDate') or '')[:10] or None,
        'expDate': (xtag(block,'expirationDate') or '')[:10] or None,
        'gemeinde': (xtag(mun,'de') if mun else None) or (xtag(reg,'town') if reg else None),
        'municipalityId': xtag(mun,'key') if mun else None,
        'registrationOffice': xtag(reg,'displayName') if reg else None,
        'bauherr': bauherr,
        'bauherrAdresse': bauherr_adr,
        'projektverfasser': pv,
        'bauobjekt': bo,
        'beschreibung': beschreibung[:300] if beschreibung else None,
        'typ': detect_type(beschreibung or ''),
        'lat': lat, 'lon': lon,
        'deadlineFrom': df, 'deadlineTo': dt,
        'contact': ct.group(1).strip().replace('\r\n','\n').replace('\r','\n') if ct else None,
    }


def fetch_page(page=0):
    params = "&".join(f"{k}={v}" for k,v in API_PARAMS.items())
    url = f"{API_BASE}?{params}&pageRequest.page={page}"
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/xml, text/xml, */*',
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8')


def parse_xml(xml):
    blocks = re.split(r'</commented>\s*</content>\s*<content>\s*<meta>', xml)
    results = []
    for i, block in enumerate(blocks):
        if i > 0: block = '<meta>' + block
        try:
            p = parse_publication(block)
            if p: results.append(p)
        except Exception as e:
            print(f"  WARN: block {i}: {e}", file=sys.stderr)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--from-file', '-f', help='Parse local XML instead of API')
    parser.add_argument('--output', '-o', default=OUTPUT_PATH)
    args = parser.parse_args()

    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}] Baugesuch-Fetcher starting")

    if args.from_file:
        print(f"  Source: {args.from_file}")
        with open(args.from_file, 'r', encoding='utf-8') as f:
            data = parse_xml(f.read())
    else:
        print(f"  Source: {API_BASE}")
        data = []
        for page in range(MAX_PAGES):
            print(f"  Fetching page {page}...")
            xml = fetch_page(page)
            page_data = parse_xml(xml)
            if not page_data:
                break
            data.extend(page_data)
            if len(page_data) < 100:
                break  # Last page

    wc = sum(1 for d in data if d['lat'])
    gm = sorted(set(d['gemeinde'] for d in data if d['gemeinde']))
    print(f"  Result: {len(data)} Baugesuche, {wc} with coords, {len(gm)} Gemeinden")

    # Wrap in envelope with metadata
    output = {
        "meta": {
            "fetchedAt": ts,
            "source": "amtsblatt.sz.ch",
            "tenant": "kabsz",
            "rubric": "BA-SZ05",
            "count": len(data),
            "withCoords": wc,
        },
        "data": data,
    }

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  Written: {args.output} ({os.path.getsize(args.output)} bytes)")
    print(f"[{datetime.now(timezone.utc).isoformat()}] Done")


if __name__ == '__main__':
    main()
