# Ingest Sorter: notes for Claude

Ingest Sorter is a local macOS app that sorts, tags, rates and approves video footage before the edit, then sends the selects to DaVinci Resolve. The owner is Shane Hulbert (GitHub: shanehulbert). Explain changes in plain language, by what will be seen on screen, not by what changed in the code.

## How to work here

- **Read the code before changing it.** `sorter/static/index.html` is one large single-page app with dense, one-line functions. Search for the function, read it, then make a small exact edit.
- **Small changes, one at a time.** Commit after each working change with a plain-language message.
- **Say what was tested and what wasn't.** If you could not see it on screen or run it in Resolve, say so.
- **Never touch footage.** Don't move, rename or delete anything on the Video Drive or in a footage folder. Test with copies or generated clips in a temp folder.
- **Never commit personal data.** The library (projects, people, faces, API key) lives outside this folder, in `../library`. Keep real names and personal paths out of code, examples and docs.
- **Restart rule:** Python changes need the app restarted. Changes to `index.html` only need a browser refresh. Tell him which applies.

## Layout

```
Open Ingest Sorter.command   launcher: runs sorter/server.py, opens http://localhost:8765
Check Setup.command          runs sorter/check_setup.py
sorter/server.py             HTTP server (Python standard library only), all /api routes
sorter/library.py            library.json, catalogues, projects, ingest pipeline, Send, vocabulary, people
sorter/export.py             effective() values, metadata, CSV, FCPXML, the generated Resolve Lua script
sorter/tagger.py             AI describer: Ollama (local) and Claude API
sorter/faces.py              people recognition (OpenCV YuNet + SFace)
sorter/ingest.py             ffprobe/ffmpeg, Sony XML, ExifTool
sorter/motion.py             camera-movement measurement (OpenCV)
sorter/dashboard.py          numbers for the Dashboard tab
sorter/static/index.html     the whole front end (HTML, CSS, JS in one file)
resolve-tests/               small Lua scripts for checking what Resolve scripting allows
docs/REFERENCE.md            detailed feature notes, by version
```

No build step and no third-party Python packages are required. `opencv-python-headless` and `numpy` are optional (movement and people). ffmpeg and ffprobe are required. ExifTool and Ollama are optional.

Run it: `python3 sorter/server.py` (options: `--port`, `--no-browser`, `--library <folder>`).
Test against a throwaway library: `python3 sorter/server.py --port 8799 --no-browser --library /tmp/testlib`.

## Data model

- `library/library.json`: projects, vocabulary, people, settings, blocked keywords.
- `library/projects/<id>/catalogue.json`: `{"clips": {id: clip}}`, plus `thumbs/`, `proxies/`, `faces/`.
- A clip has `meta` (read from the file), `ai` (describer output), `review` (the user's own values), `motion`, `faces`.
- **`review` always wins over `ai`.** Never overwrite what the user typed. `export.effective(clip, ctx)` is the single place that merges them. Use it; don't re-derive.
- Statuses: `unreviewed`, `approved`, `rejected`, `ingested` (moved to the destination).
- Project `mode`: `"shoot"` or `"archive"` internally. **On screen these are "Managed Media" and "Linked Media".** Never show the words Shoot or Archive.
  - Managed Media: footage in `01_Footage/00_Rushes`; Send moves approved clips to `01_Footage/01_Selects`.
  - Linked Media: Send moves nothing. A sent clip keeps status `approved` and gets `clip["sent"]`; the UI shows it green as "sent" (`vstat()` in the front end, the same rule in `dashboard.py` and `/api/library`).
- Resolve files go to `<project folder>/02_Project/_Resolve Import`.

## DaVinci Resolve facts (free version, tested on Resolve 21, macOS)

- Scripts in `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/` appear under Workspace > Scripts without a restart.
- Get the app object with `resolve or (Resolve and Resolve()) or (app and app:GetResolve())`. A bare `Resolve()` fails silently when run from the menu.
- Menu scripts print nothing to the Console, and can't read files (`io` is blocked). The data is embedded in the generated Lua. To report from a test script, create bins.
- These work: `ImportMedia`, `SetName`, `SetClipProperty`, `SetMarkInOut` (then `pm:SaveProject()`), `SetClipColor`, `SetMetadata` (most fields, including Rating, Scene, Shot, Keywords, Description, People, "Day / Night"), `DuplicateTimeline`, `AppendToTimeline`.
- These don't: `SetMetadata("Good Take", ...)`, `SetClipProperty("Rating", ...)`.
- Clips are matched by full file path, falling back to file name only when the name is unique.
- One draft timeline per project ("<Project> - draft"); later Sends append only new clips.

## Interface rules Shane has set

- Toggle switches, not checkboxes.
- Keep the stats row, keyword filter and filter row above the clip browser. Keep the Tool Panels (Library, Filter, AI, Metadata, Send, Settings) with their icons and names.
- Colours: orange `#e8873a` accent and selected state, blue approved, green ingested or sent, red rejected, purple for filled metadata. See `:root` in `index.html`.
- Class names in `index.html` are global. Prefix new ones (the Dashboard uses `d...`) and check a name isn't already used: `.row` and `.r` have clashed before.
- Australian English in all user-facing text. Plain words; explain a term the first time.
- Mock up bigger interface changes and agree them before building.

## Not done yet

- Speech transcription and dialogue search.
- Several selects per clip (markers or subclips).
- Watch folders.
- A proper Mac app bundle ("Ingest Sorter.app") and renaming the launcher.
- Windows and Linux (the folder picker and Resolve script install are Mac-only).
- The Claude prices in `tagger.py` (`CLAUDE_MODELS`) were written by hand and have not been checked against Anthropic's price list.
