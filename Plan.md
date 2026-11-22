# Projekt-Plan: Second Brain AI-Architect & Personal RAG System (macOS)

## 1. Projektziel & System-Architektur
Entwicklung einer lokalen, browserbasierten KI-Assistenz (Streamlit Web-App auf macOS), die das bestehende Obsidian-Vault (Second Brain auf Google Drive) durchsuchbar macht, neue Erkenntnisse/Pläne zurück in Obsidian schreiben kann und gleichzeitig volle Flexibilität wie die normale Gemini-App bietet (allgemeines Wissen + Live-Internetsuche + RAG + multimodale Uploads).

**Kern-Funktionen:**
* **All-in-One Flexibilität (Hybrid Agent):** Kombiniert in jedem Chat-Durchgang automatisch (a) eigenes LLM-Wissen, (b) Live-Google-Suche für aktuelle Tech-Infos und (c) semantische Suche im Obsidian-Vault.
* **Context-Aware Folder-Routing:** Kann je nach gewähltem Fokus gezielt bestimmte Ordner im Vault priorisieren (`System_Engineering`, `Studium`, `Prompts`, `Projekte` vs. `Inbox`/`Reflexion`), ohne harte Blockaden aufzubauen.
* **Human-in-the-Loop Write-Back:** Kann nach Rückfrage und Bestätigung im Chat neue Notizen, Prompts oder GitHub-Checklisten direkt als `.md`-Datei ins Obsidian-Vault schreiben.
* **Projekt-Plan-Generator & Export:** Entwickelt im Dialog (basierend auf PM-Wissen aus `Studium` + Coding-Standards aus `System_Engineering`) strukturierte `PLAN.md`-Dateien und exportiert sie wahlweise als Word-Dokument (`.docx`) zum Bearbeiten oder direkt als Markdown-Datei ins Vault.

---

## 2. Tech-Stack (Leichtgewichtig für MacBook)
* **OS & Umgebung:** macOS (Apple Silicon / Intel), Python 3.11+ (`venv`)
* **Frontend / Chat-UI:** `streamlit` (läuft lokal im Browser unter `http://localhost:8501`)
* **AI SDK:** `google-genai` (Offizielles Google GenAI SDK – kein veraltetes `google-generativeai` verwenden!)
* **LLM:** Über `GEMINI_MODEL` in `.env` konfigurierbar (Standard z. B. `gemini-2.5-flash` oder `gemini-3-flash` – vor Phase 3 exakte verfügbare Flash-Modell-ID gegen die Google GenAI API prüfen)
* **Embeddings:** `gemini-embedding-001` (API-basiert -> kein lokaler PyTorch-Download, spart >1.5 GB Speicherplatz)
* **Vektordatenbank:** `chromadb` (lokale Persistenz im Projektordner `chroma_db/`)
* **Dokumenten-Export:** `python-docx` (generiert saubere `.docx`-Dateien aus Markdown-Projektplänen)
* **Config:** `python-dotenv` für API-Keys, Modell-ID und Pfade in `.env`

---

## 3. Projektstruktur
```text
second-brain-architect/
├── .env                  # GEMINI_API_KEY, GEMINI_MODEL, OBSIDIAN_VAULT_PATH, EXPORT_DIR, DEFAULT_SAVE_FOLDER
├── .gitignore            # Ignoriert .env, venv/, chroma_db/, sync_state.json, exports/, chats/
├── requirements.txt      # google-genai, chromadb, streamlit, python-dotenv, python-docx
├── PLAN.md               # Dieser Bauplan
├── README.md             # Dokumentation für GitHub-Portfolio
├── sync_state.json       # Speichert Dateipfade + mtime-Timestamps für Incremental Sync
├── chroma_db/            # Lokale Vektordatenbank
├── exports/              # Temporärer Speicher für generierte Dateien (.docx, .md, .txt)
├── chats/                # Lokale JSON-Dateien für dauerhafte Chat-Historien
└── src/
    ├── config.py         # Lädt .env-Variablen & validiert Google-Drive-Pfad
    ├── ingest.py         # Incremental Sync (Timestamp-Check, Chunking, Metadaten-Tagging)
    ├── tools.py          # Python-Tools: search_vault(), propose_note_save(), create_downloadable_file() & sanitize_filename()
    ├── exporter.py       # Konvertiert Markdown-Projektpläne in formatierte .docx-Dateien
    ├── chat_storage.py   # Speichert & lädt Chat-Verläufe mit Titel & Zeitstempel als JSON
    └── app.py            # Streamlit Chat-UI mit Sidebar, Multimodal-Upload & Export-Buttons


4. Umsetzungs-Phasen (Schritt-für-Schritt)
Phase 1: Setup, Sicherheit & Vault-Vorbereitung
[x] Projektstruktur, venv, requirements.txt und .gitignore (inkl. chats/, exports/, chroma_db/, .env, sync_state.json) anlegen.
[x] .env-Vorlage erstellen mit GEMINI_API_KEY, GEMINI_MODEL="gemini-2.5-flash", OBSIDIAN_VAULT_PATH (Pfad zu .../Second Brain) und DEFAULT_SAVE_FOLDER="System_Engineering".
[x] In src/config.py sicherstellen, dass der Google-Drive-Pfad existiert und lesbar ist (inkl. verständlicher Fehlermeldung, falls Google Drive Desktop nicht gemountet ist).
[x] Manueller Schritt im Vault: Im Ordner Second Brain den neuen Hauptordner System_Engineering anlegen (für Coding-Standards, GitHub-Publish-Routine, Projekt-Learnings und Projektpläne).
Phase 2: Incremental Sync & Metadaten-Indexing (src/ingest.py)
[x] Alle .md-Dateien rekursiv im OBSIDIAN_VAULT_PATH scannen (Systemordner wie .obsidian, .trash und Anhänge überspringen).
[x] Metadaten extrahieren: Für jeden Chunk den obersten Ordnernamen als category speichern (z. B. Inbox, System_Engineering, Studium, Projekte, Prompts) sowie filename und last_modified.
[x] Incremental Sync: Über os.path.getmtime() gegen sync_state.json prüfen. Nur neue oder geänderte Dateien verarbeiten; bei geänderten Dateien zuerst alte Chunks dieser Datei aus ChromaDB löschen.
[x] Smart Chunking: Markdown nach Überschriften (#, ##, ###) und Absätzen in Chunks von ca. 600–800 Zeichen teilen (mit 100 Zeichen Overlap) und via gemini-embedding-001 in ChromaDB speichern.
Phase 3: Agent-Tools, Export & Write-Back-Logik (src/tools.py & src/exporter.py)
[x] Word-Exporter (src/exporter.py): Funktion markdown_to_docx(markdown_text: str, filename: str) -> str mit python-docx schreiben. Überschriften (#, ##, ###), Bullet-Points, Code-Blöcke (Monospace-Schriftart) und Fettgedrucktes sauber umwandeln und unter exports/ speichern.
[x] Dateinamen-Bereinigung (sanitize_filename): Hilfsfunktion in src/tools.py, die ungültige macOS-Zeichen (wie :, /, \) aus von Gemini vorgeschlagenen Dateinamen entfernt.
[x] Tool 1 (search_vault): Durchsucht ChromaDB semantisch. Optionaler Parameter folder_filter (z. B. nur in System_Engineering + Studium + Prompts suchen, oder global über das ganze Vault). Gibt Text-Chunks inkl. Dateiname zurück und loggt genutzte Dateinamen zusätzlich in einer strukturierten Liste (z. B. über st.session_state) für die UI-Quellenanzeige und chat_storage.py.
[x] Tool 2 (propose_note_save & execute_note_save): Ermöglicht es Gemini, einen neuen Inhalt (z. B. einen neuen Prompt, ein Learning oder eine PLAN.md) inkl. Zielordner und bereinigtem Dateinamen vorzuschlagen. Gespeichert wird erst nach expliziter User-Bestätigung in der UI.
[x] Tool 3 (create_downloadable_file): Generiert auf Anfrage des Users (z. B. „Gib mir das als Word-Datei“) eigenständig .docx-, .md- oder .txt-Dateien in exports/ und gibt den Dateipfad für den Download-Button in der UI zurück.
[x] Combined Tool Calling: In der GenerateContentConfig des google-genai SDKs sowohl die eigenen Python-Tools als auch das Built-in-Tool google_search kombinieren, damit das Modell nahtlos zwischen Internetwissen, eigenem Wissen und Obsidian-RAG wechseln kann. (Hinweis: Falls die Gemini API das gleichzeitige Aktivieren von Built-in google_search und eigenen Python-Tools in einem Request einschränkt, auf ein sauberes Two-Step-Routing bzw. getrennte Tool-Aufrufe ausweichen.)
Phase 4: Dauerhafte Chat-Historie (src/chat_storage.py)
[x] Speichert jeden Chat automatisch mit generiertem Titel, Zeitstempel, Nachrichtenverlauf und genutzten Quellen als JSON-Datei unter chats/.
[x] Stellt Hilfsfunktionen bereit zum Auflisten aller gespeicherten Chats (sortiert nach Aktualität), Laden eines bestehenden Chats und Anlegen eines neuen Chats.
Phase 5: Streamlit Browser-Interface (src/app.py)
[x] Auto-Sync & Sidebar:
Beim ersten Start einer Session automatisch einmal ingest.py ausführen (damit z. B. per Telegram hinzugefügte und über Google Drive synchronisierte Notizen sofort verfügbar sind), plus manueller Button „Vault jetzt synchronisieren“ mit Anzeige der geladenen Änderungen.
Button „+ Neuer Chat“ sowie eine Liste der bisherigen Chats aus chat_storage.py zum Wechseln.
Fokus-Auswahl (beeinflusst System-Prompt & Such-Priorisierung, blockiert aber nie allgemeines Wissen):
Allrounder (Standard): Voller Zugriff auf alles (Brain + Internet + Allgemeinwissen).
System-Engineering & Projekt-Architekt: Priorisiert System_Engineering, Studium, Projekte & Prompts; hilft beim Ausarbeiten von Ideen bis zur fertigen PLAN.md inkl. GitHub-Routine.
Interview- & Reflexions-Coach: Priorisiert Inbox & Projekte; extrahiert STAR-Stories oder analysiert Muster ehrlich und ungeschönt.
[x] Hauptbereich (Chat & Multimodale Inputs):
Chat-Verlauf mit st.chat_message.
Multimodaler Input via st.chat_input(accept_file=True) für Bilder (.png, .jpg, .webp) und Dokumente (.pdf, .txt, .md, .csv, .docx). Bilder und PDFs werden direkt multimodal an Gemini übergeben; .docx und .csv werden in Python extrahiert und dem Prompt beigefügt.
Aufklappbare Box unter jeder Antwort (st.expander("Genutzte Obsidian-Quellen & Web-Links")), befüllt aus den mitgeloggten Quellen aus tools.py und den Grounding-Metadaten von google_search.
State-sichere Action-Buttons: Ausstehende Speicher-Vorschläge (propose_note_save) und generierte Dateien (create_downloadable_file) sauber über st.session_state verwalten, sodass Klicks auf „Bestätigen & ins Vault speichern“ oder „Datei herunterladen“ bei Streamlit-Reruns nicht verloren gehen.
Phase 6: GitHub-Publish-Routine (Dogfooding!)
[ ] Das fertige Projekt nach den eigenen Regeln aus System_Engineering auf GitHub veröffentlichen: .gitignore prüfen (keine .env, keine chroma_db/, keine chats/), saubere README.md mit Architektur-Schaubild (Mermaid.js) und Screenshots erstellen.
5. Regeln für den Coding-Assistenten (Vibe-Coding-Guardrails)
Schrittweise Umsetzung: Arbeite immer nur eine Phase ab, erkläre kurz die Architektur-Entscheidungen und warte auf mein Okay, bevor du mit der nächsten Phase beginnst.
Kein Over-Engineering: Kein LangChain/LlamaIndex – direktes google-genai SDK + chromadb, damit jeder Code-Teil transparent und im Bewerbungsgespräch leicht erklärbar bleibt.
Datenschutz-First: Niemals echte Vault-Inhalte oder API-Keys in Logs oder Git-Dateien schreiben.
