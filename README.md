# Baugesuch-Karte Kanton Schwyz

Interaktive Karte aller aktuellen Baugesuche im Kanton Schwyz.


## Architektur

```
index.html ──fetch()──→ data/baugesuche.json
   (fix)                    (wird 2x täglich aktualisiert)
                                    ↑
                          GitHub Action (Cron)
                                    ↑
                          amtsblatt.sz.ch REST-API
```

- **index.html** – Festes UI-Grundgerüst. Ändert sich nie.
- **data/baugesuche.json** – Daten mit Metadaten-Envelope. Wird 2x täglich vom Cron-Job aktualisiert.
- **fetch_baugesuche.py** – Holt Daten von der API, parst XML, schreibt JSON. Kein externer Dependency.

## Datenquelle

REST-API des SECO Amtsblattportals, Kanton Schwyz:

```
GET https://amtsblatt.sz.ch/api/v1/publications
    ?tenant=kabsz
    &subRubrics=BA-SZ05
    &publicationStates=PUBLISHED
    &includeContent=true
```

- Rubrik **BA-SZ05** = Baugesuch
- Koordinaten im **LV95-Format** direkt aus der Projektbeschreibung → Umrechnung WGS84
- Keine Authentifizierung nötig (öffentliche Daten)

## Lokal testen

```bash
# Daten holen (oder --from-file für lokale XML)
python fetch_baugesuche.py

# Lokalen Server starten (wegen fetch/CORS)
python -m http.server 8000
open http://localhost:8000
```

## Deployment (GitHub Pages)

1. Repository erstellen, Code pushen
2. Settings → Pages → Source: `main`, Ordner: `/ (root)`
3. Optional: Custom Domain in Settings → Pages eintragen
4. GitHub Action läuft automatisch 2x täglich
5. Manuell: Actions → "Update Baugesuche" → "Run workflow"

## Projektstruktur

```
├── index.html                    # UI (ändert nie)
├── data/
│   └── baugesuche.json           # Daten (automatisch aktualisiert)
├── fetch_baugesuche.py           # API-Fetcher + XML→JSON Parser
├── .github/
│   └── workflows/
│       └── update.yml            # Cron-Job (2x täglich)
└── README.md
```

## Lizenz

MIT – Daten: Amtsblatt Kanton Schwyz (öffentlich zugänglich)
