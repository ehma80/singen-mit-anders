# Singen mit anders

Chor-Übungs-App: Noten hochladen → jede Stimme wird **mit Text** vorgesungen oder am Klavier gespielt → die eigene Stimme ausschalten („Ich singe“) und selbst singen.

Die App läuft kostenlos auf **GitHub Pages**. Noten und Aufnahmen liegen dort nur **verschlüsselt** – ohne das Chor-Passwort kann niemand etwas damit anfangen, auch wenn das Repository öffentlich ist.

## Adressen
- Für die Sängerinnen: `https://lieder.grammarbox.de/` (auch im Singtrainer unter „Unsere Lieder“)
- Für die Chorleitung (mit Hochladen): `https://lieder.grammarbox.de/#chorleitung`
- Ein Stück direkt öffnen: `https://lieder.grammarbox.de/#jungs-die-singen`
- Öffentlich ist nur die Titelliste `titles.json` (Titel und Stimmen, für die Startseite des Singtrainers). Noten und Aufnahmen bleiben verschlüsselt.
- Die alte Adresse `ehma80.github.io/singen-mit-anders/` leitet automatisch weiter. Die eigene Domain steht in der Datei `CNAME` im Branch `site`.

## Einmalig einrichten
1. **Passwort festlegen:** Repository → *Settings* → *Secrets and variables* → *Actions* → *New repository secret*.
   Name: `CHOR_PASSWORT`, Wert: das Chor-Passwort. (Nur hier eintragen, nirgends sonst aufschreiben.)
2. **Erste Seite bauen:** *Actions* → „Stücke einsingen“ → *Run workflow*. Dauert beim ersten Mal ca. 10 Minuten.
3. **Pages einschalten:** *Settings* → *Pages* → *Source: Deploy from a branch* → Branch `site`, Ordner `/ (root)` → *Save*.

## Neues Stück hinzufügen
1. `…/#chorleitung` öffnen, Passwort eingeben, auf **➕ Stück hochladen** tippen und die Noten-PDF wählen.
   Die PDF wird auf dem Gerät verschlüsselt und als `Name.pdf.enc` gespeichert.
2. Auf **„Datei auf GitHub hochladen“** tippen, die `.enc`-Datei in das Feld ziehen, *Commit changes*.
3. GitHub singt das Stück automatisch ein (je nach Länge 30–60 Minuten). Danach Seite neu laden.

Ein Stück entfernen: die `.enc`-Datei im Ordner `noten/` löschen.

**Wichtig:** Keine unverschlüsselten PDFs in `noten/` legen – das Repository ist öffentlich.

## Technik
- `server/vecomr.py` – liest digital erzeugte Noten-PDFs (Sibelius/Finale, Opus- und Norfolk-Schriften) direkt aus den Vektordaten: Tonhöhen, Rhythmen, Triolen, Bindebögen, Liedtext.
- `server/score.py` – Notenmodell, MusicXML-Import, Text-Reparatur, MusicXML-Export fürs Notenbild.
- `server/nsynth.py` – KI-Gesang: deutsche Piper/VITS-Stimmen, Lautdauern und Tonhöhe exakt nach den Noten (WORLD-Vocoder); Chor = 3 Sänger je Stimme.
- `server/ci_build.py` – GitHub Action: entschlüsselt neue Uploads, singt ein, verschlüsselt alles (`chorcrypt.py`, AES-256-GCM).
- `web/index.html` – Player: Notenbild (Verovio) mit Textmarker, Klavier/Gesang je Stimme, Tempo, Klick, Einsatz-Zähler, Tonangabe, Schleife, Passwortabfrage.
