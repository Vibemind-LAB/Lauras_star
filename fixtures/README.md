# fixtures

Reproduzierbare Testdaten.

## `golden/`

Byte-genaue Referenz-Exporte des kanonischen Demo-Schnitts (zwei Clips, zwei
Sprecher, 30 fps) aus den deterministischen Writern (EDL, FCP7-XML, SRT, VTT). Der
Test `services/local-api/tests/test_golden_fixtures.py` rendert den Demo-Schnitt neu
und vergleicht **byte-genau** gegen diese Dateien — so fällt jede ungewollte
Format-Drift sofort auf. OTIO wird stattdessen per Round-Trip geprüft (seine
JSON-Serialisierung ist bibliotheksversionsabhängig).

`.gitattributes` (`* -text`) verhindert CRLF-Konvertierung, damit der Byte-Vergleich
plattformübergreifend stabil bleibt.

### Nach einer beabsichtigten Format-Änderung neu erzeugen

```bash
cd services/local-api
LAURA_REGEN_GOLDEN=1 uv run pytest tests/test_golden_fixtures.py
```
