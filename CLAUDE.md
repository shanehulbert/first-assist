# First Assist: notes for Claude

First Assist (called Ingest Sorter until 23 November 2026) is a local macOS app that sorts, tags, rates and approves video footage before the edit, then sends the selects to DaVinci Resolve. The owner is Shane Hulbert (GitHub: shanehulbert). Explain changes in plain language, by what will be seen on screen, not by what changed in the code.

## How to work here

- **Read the code before changing it.** `sorter/static/index.html` is one large single-page app with dense, one-line functions. Search for the function, read it, then make a small exact edit.
- **Small changes, one at a time.** Commit after each working change with a plain-language message.
- After Shane confirms a change works, commit and push it to GitHub. Never push untested changes.
- **Say what was tested and what wasn't.** If you could not see it on screen or run it in Resolve, say so.
- **Never touch footage.** Don't move, rename or delete anything on the Video Drive or in a footage folder. Test with copies or generated clips in a temp folder.
- **Never commit personal data.** The library (projects, people, faces, API key) lives outside this folder, in `../library`. Keep real names and personal paths out of code, examples and docs.
- **Restart rule:** Python changes need the app restarted. Changes to `index.html` only need a browser refresh. Tell him which applies.

## Layout

```
Open First Assist.command   backup launcher in Terminal: runs sorter/server.py, opens http://localhost:8765
tools/make_app.sh            builds First Assist.app (default /Applications): starts the server in the background, opens a tab
branding/                    the studio icon artwork (app icon 1024, tiles); sorter/static/icons/ holds the browser-tab copies
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
If 8765 is busy the server falls back to the next free port, but never 8766 (Keyword Generator's; its Lightroom
plug-in only talks to 8766).

**Mac launcher (Stage 3, Nov 2026; same shape as Keyword Generator's).** `tools/make_app.sh` builds a small bundle
(Info.plist, a bash `launch` script, an .icns made with sips + iconutil, ad-hoc signed). The launch script has this
folder's path baked in, so rebuild after moving it. It checks `/api/version` on 8765 (`"app"` first-assist, or the
old footage-sorter → just `open` the URL), alerts if another program holds the port (so the app never ends up on a
fallback port), else starts `/usr/bin/python3 sorter/server.py --port 8765 --no-browser` with nohup (log:
`library/first-assist.log`), waits up to 15 s and opens the URL. **On Apple silicon it starts Python with
`arch -arm64`** (from Finder/the Dock the script can run under Rosetta, and the arm64 OpenCV/NumPy then fail to load;
`LSRequiresNativeExecution` is set too). `FA_APP_PORT` / `FA_APP_LIBRARY` exist only to build test copies.
**Quit** (foot of the sidebar) asks once, and again if a project's status is running; it POSTs `/api/quit` (refused
if the Origin isn't localhost). The server shuts down after replying, then **waits for every POST still in progress**
(`BUSY` counter: Send, Undo, Move back and New project move files inside one request) before exiting, so a move is
never cut off half-way. Background threads (scan, previews, AI, faces) just stop. The page shows “has stopped” and
reloads itself when the app is back (the status poll also shows that page if the server disappears).

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
- Colours: blue approved, green ingested or sent, red rejected, purple for filled metadata. See `:root` in `index.html`.
- Studio livery shared with Keyword Generator (decided 23 Nov 2026): icon = white bars and chevron on brand purple `#9333ea`
  (icon tiles only; artwork in `branding/`, browser-tab copies in `sorter/static/icons/`, served at `/icons/…` and
  `/favicon.ico`); the sidebar shows the 26 px tile beside the wordmark `#a855f7` (flat, no gradient).
  **Orange `#e8873a` (`--me`) means “your edits” only**: star ratings, and a clip card's scene line when you set the
  scene yourself. The general accent (`--accent`, `--proj`) is neutral off-white `#e8e6e1` (primary buttons with dark
  text, selected segments, the Library band, progress bars); focus rings are blue `--set`; running status is grey;
  the selected project and Managed tag are neutral grey. AI colours (`#a58cf0` and its shades), the Filter tab's yellow,
  warnings (amber) and Resolve clip colours (A-roll green, B-roll apricot) are unchanged. Don't use purple as a general accent.
- Class names in `index.html` are global. Prefix new ones (the Dashboard uses `d...`) and check a name isn't already used: `.row` and `.r` have clashed before.
- Australian English in all user-facing text. Plain words; explain a term the first time.
- Mock up bigger interface changes and agree them before building.

## Not done yet

- Speech transcription and dialogue search.
- Several selects per clip (markers or subclips).
- Watch folders.
- Windows and Linux (the folder picker and Resolve script install are Mac-only).
- The Claude prices in `tagger.py` (`CLAUDE_MODELS`) were written by hand and have not been checked against Anthropic's price list.
