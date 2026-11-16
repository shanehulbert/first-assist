# Ingest Sorter: detailed reference

Detailed notes on each feature, written as the versions were built. Start with the [README](../README.md); where the two differ, the README is current.

AI sorting and describing of your footage: sort, search, rate and approve video footage **before** it goes into an editing project, then send the approved selects to DaVinci Resolve with their metadata and a draft timeline.

Everything runs locally on your Mac.

## How it works

1. **New project:** give it a name, a **source folder** (ingested footage, on any drive) and a **destination folder** (where approved clips will go). You can add more footage folders to a project later.
2. **Processing:** the app reads each clip's technical metadata (camera, lens, timecode, exposure, GPS and Blackmagic Camera app fields), looks up a place name from the GPS, and makes thumbnails and preview proxies. Counters show progress.
3. **Review (Clips tab):** browse, search and filter (scene, status, rating, role, shot type, camera movement, camera/lens), use the **Keyword Filter** (pinned keywords with counts; click to filter, × to clear), play clips with a live source timecode readout, set in/out points, rate, and approve or reject. Approved clips are **A-roll** unless you mark them **B-roll** (B key). Each clip has a **Scene**, a short **Title**, **Shot type** (EST, EWS, WS, MWS, MS, MCU, CU, ECU, OTS, Insert), **Angle**, **Camera movement** (measured automatically, with a confidence score, and you can override it) and **Keywords**. Original files are never renamed. Instead, each clip gets a **Clip Name** for Resolve (Scene · Shot · Title · Clip number, editable).
4. **Controlled Vocabulary (Vocabulary tab):** the approved keyword list shared by all projects. Add synonyms (typed variations are swapped for the main keyword), pin keywords to the filter bar, and rename or merge keywords (every clip in every project is updated). Keywords in use that aren't in the vocabulary are listed so you can add them or map them onto an existing keyword.
5. **Send approved to Resolve:**
   - **moves** approved clips into the destination. On another drive, each clip is copied, verified (size and checksum) and only then removed from the source. Rejected clips stay where they are.
   - writes the Resolve import files into `_Resolve import` inside the destination:
     - `Ingest Sorter - Apply Last Send.lua`: Send installs this in Resolve's Scripts folder (it appears straight away, no restart). In Resolve, **select your media bin and run Workspace > Scripts > Ingest Sorter - Apply Last Send**. It works in the free version of Resolve, and it:
       - imports any approved clips not yet in the Media Pool into that bin
       - renames each clip to its Clip Name, sets its in/out marks, colours it (A-roll **Green**, B-roll **Apricot**) and **writes its metadata** (Scene, Shot, Shot Type, Shoot Day, People, Description, Keywords, Comments, Rating, Angle, Move and any Metadata panel fields). Clips not yet in the Media Pool are imported from where they are into the selected bin
       - builds the draft timeline by **duplicating your template timeline** (set under Settings > Resolve draft timeline; default: the first timeline in `00_Timelines/00.01_Working`) and placing the clips on **V1**, in recording order, trimmed to their in/out, with their audio on A1. With no template it makes a plain timeline.
       - saves the project.
       To rebuild it for a project's last Send: `python3 sorter/install_script.py <project-id>`.
     - `<Project> - metadata.csv`: a backup of the same metadata. You only need it if the script can't run: **File > Import Metadata To > Media Pool** (match by file name; untick the timecode match). Fills Scene, Shot, Shoot Day, People, Description, Keywords, Comments and Rating; role, scene, movement and keywords become keywords, which become keyword smart bins.
     - `<Project> - draft timeline <date>.fcpxml`: a backup single-track timeline (**File > Import > Timeline**) for use without the script.
     - `<Project> - catalogue report.csv`: every clip, its status and where it now lives.
   - **Undo last send** puts the clips back.

## People, naming and settings (v1.4)

- **People** (sidebar and People tab) are shared across all projects. Add people to a clip in the clip panel, or pause on a good frame, click **Crop face**, drag a box around the face and type the name. People go to Resolve's People field.
- **Clip names** are built from tokens in **Settings** (Project, Person, Title, Scene, Shot, Day, Date, Clip, Camera, Reel, Role, Location). Drag them to reorder and choose the separator. The default is Title_Day_Shot_Clip, e.g. `Couple-dance_D2_MS_C076`.
- **Project details:** name, client, and a default location for clips without GPS.
- **Camera movement confidence:** green (sent to Resolve), yellow (worth a look) or red (needs your input), with a **Confirm** button. Thresholds are set in Settings.
- **JKL** transport: L plays (tap again for 2×, 4×), J plays in reverse, K stops, and holding K with J or L steps one frame.

## Layout (v1.7.1)

- **Left: Tool Panels.** Library (projects, footage folders), Filter (every filter with counts, linked to the filter bar above the clips), AI (describe, tidy scenes, find people and their settings), Send (Send to Resolve, the steps in Resolve, the draft timeline template) and Settings (project details, clip-name tokens, confidence thresholds). The app remembers which panel was open.
- **Centre:** the Clips, Vocabulary and People tabs, with the stats, Keyword Filter and filter bar above the clips.
- **Right:** the clip panel.

## Viewer and Metadata (v1.8)

- **Viewer tab:** one clip at a time, large. The clip panel moves into the Viewer: big video with all the playback and in/out controls on the left, review fields (clip name, Approve / Reject / Unreviewed, stars, role, people, shot, scene, title, description, keywords…) on the right, and a filmstrip of the clips matching the current filters along the bottom. ← → step through; A / X / U decide and move on. Double-click a clip in the Clips tab to open it here.
- **Metadata tool panel:** fills Resolve's own metadata fields (Shot & Scene, Production, Crew, Second unit, Notes), with dropdowns for Day / Night and Environment and suggestions from values already used. Fill a clip once, then **Copy to All / Approved / Shown**, for everything or per section. Only filled fields are copied. Field names match Resolve exactly, so they import with the metadata CSV. The CSV also now carries Shot Type, Rating, Angle and Move.

## AI describer (v1.6)

The describer looks at a few frames from each clip and fills in its **Scene, Title, Description, Keywords, Shot type, Angle** and a **suggested in/out**. It prefers terms already in your Controlled Vocabulary and reuses the project's existing scene names. Anything you've typed yourself is never overwritten: the AI only fills the suggested values underneath your edits.

Choose an engine under **Settings › AI describer**:

| | Local | Claude |
|---|---|---|
| Runs | on your Mac, through [Ollama](https://ollama.com) | Anthropic's API (internet) |
| Cost | free | about US$0.0006 a clip with Haiku 5.5, about US$0.013 with Sonnet 5.5 |
| Quality | good for "what's in the shot"; weaker on nuance | close to a human assistant editor |
| Privacy | nothing leaves your Mac | frames are sent to Anthropic |

**Local setup:** install Ollama from ollama.com and open it, then pull a vision model in Terminal, for example `ollama pull qwen2.5vl:7b` (about 6 GB; fine on 16 GB of memory). Bigger models describe better but need more memory. The app lists every vision model Ollama has installed.

**Claude setup:** create an API key at platform.claude.com (API usage is billed separately from a Claude chat plan; Pro plans need prepaid credit, Max/Ultra plans include monthly API credit). Paste it into Settings › AI describer. It's checked, then stored only on this Mac (`library/secrets.json`, outside the app folder), or set the `ANTHROPIC_API_KEY` environment variable instead.

**Running it:**
- **✦ Describe with AI** at the top of the clips: choose clips without a description, the clips currently shown, or all clips. It uses the engine chosen in Settings. Claude runs show an estimated cost first and the running cost while they work. **Stop** halts after the clips in progress.
- **✦ Describe again** beside a clip's Description redoes just that clip.
- **Tidy scenes** (beside Describe) merges similar scene names across the whole project, using clip titles and recording times. It also runs automatically after describing more than one clip. Scenes you typed yourself are kept and used as anchors.
- **Automatic** (the switch in Settings) describes new footage at the end of each import.

## People recognition (v1.7)

Off by default. Turn it on under **Settings › People recognition** and click **Download face models** once (two small OpenCV models, about 40 MB, saved in `library/models`). It needs OpenCV (`pip3 install --user opencv-python-headless`). Everything runs on your Mac; no faces or images are sent anywhere.

- After processing (or with **Find people** at the top of the clips) it looks at a few frames of each clip, finds faces and makes a "faceprint" of each.
- **Known people** are tagged automatically with a confidence light (green / orange / red, using the same thresholds as camera movement). Click **✓** to confirm or **×** if they're not in the clip. Only confirmed or green people are sent to Resolve and used in clip names.
- **Faces to name** (top of the People tab) groups faces that don't match anyone. Type a name (new or existing) to tag every clip in the group, or **Reject** to ignore that face from now on. **show** filters the clips to that group.
- Each person's face photo (Crop face), every group you name and every match you confirm improve recognition.
- Small, blurred, side-on or badly lit faces are often missed. Faceprints are stored in `library/people/faceprints.json` and `library/projects/<id>/faces.json`.

## Setup (macOS)

- Python 3: comes with Apple's command line tools (`xcode-select --install`). If Xcode is installed, accept its licence once: `sudo xcodebuild -license accept`.
- ffmpeg, for reading and processing footage: `brew install ffmpeg` (install Homebrew from https://brew.sh if needed). No admin rights? Put static `ffmpeg` and `ffprobe` builds in the `bin` folder instead (see `bin/README.txt`). The app shows a red banner if they're missing.

Optional, for measuring camera movement: `pip3 install --user opencv-python-headless`. Without it, everything else still works and movement can be set by hand.

Optional, for fuller camera details: `brew install exiftool`. When it's installed, the Sorter also reads settings that ffmpeg can't see (for example aperture, ISO and shutter from Sony's frame-by-frame camera data, and lens and focal length on cameras that record them). Existing clips are re-read once after it's installed.

## Use

Double-click **Open Ingest Sorter.command**, or run `python3 sorter/server.py`. Then work in the browser at http://localhost:8765. Keep the Terminal window open while you work.

Projects and their thumbnails and proxies live in a `library` folder beside this code folder.

### Shortcuts

| Key | Action |
|---|---|
| ← → | Previous / next clip |
| Space | Play / pause |
| I / O | Set in / out at the playhead |
| P | Play from in to out |
| 1–5, 0 | Rate / clear rating |
| A / X / U | Approve / reject / unreviewed |
| B | Approve as B-roll (or switch an approved clip between A-roll and B-roll) |
| C | Copy the clip name to the clipboard (also the Copy button next to the name) |

## Roadmap

- Local vision-model tagger (Apple Silicon) that uses your term library, with an optional cloud tagger for big batches and a cost estimate shown first
- Plain-language search
- Speech transcription
- Shot detection for long clips
- Saved searches

## Projects and Send modes (v1.9)

**New project** asks how the footage arrives. The project list tags each project **Managed** or **Linked**, and you can switch or rename a project from the Library panel:

- **Managed Media (moves selects).** Pick the project folder. The Sorter creates `01_Footage/00_Rushes`, `01_Footage/01_Selects`, `02_Project/_Resolve Import` and `03_Deliverables`, and offers to move any loose clips into `00_Rushes`. Put new footage in `00_Rushes`. **Send** moves approved clips to `01_Selects`, keeping card or day sub-folders. Rejects stay in `00_Rushes`; nothing is deleted.
- **Linked Media (leaves footage in place).** Add any number of footage folders (for example, from an archive drive). **Send moves nothing**: Resolve imports the clips from where they are. Optionally choose a folder for the edit, which gets `02_Project/_Resolve Import` and `03_Deliverables`.

Either way, the Resolve files (script, metadata CSV, catalogue report and backup FCPXML timeline) go to `02_Project/_Resolve Import`. Projects made before v1.9 keep the old behaviour.

The scanner skips `iMovie Cache`, `iMovie Thumbnails`, `_Resolve Import`, `02_Project` and `03_Deliverables`.

`resolve-tests/Ingest Sorter - Metadata Test.lua` checks whether a script can write clip metadata directly in your version of Resolve.

### Sending again, and project details (v1.9)

- The script matches clips by their **full file path**, so two clips with the same name (e.g. IMG_0101.MOV from different years) are never confused. The file name is used only when the path doesn't match and the name is unique.
- Each project has **one draft timeline** ("<Project> - draft"). The first Send builds it from your template; later Sends add only the newly approved clips to the end. Renaming the project keeps the same timeline.
- Running the script again updates names, marks, colours and metadata on clips already in Resolve. The app's values win. Empty fields are skipped, and rejected clips are left alone.
- **Project details** (New project window, or the top of the Metadata panel): Production Name, Program, Series, Episode, Genre, Production Company, Producer, Director, DOP, Camera Operator, Sound Mixer, Editor and Location. Every clip inherits them; a value typed on a clip wins for that clip. Production Name defaults to the project name.

## Dashboard (v1.10)

A fifth tab after People, for **This project** or **All projects**.

- **Still to do:** clips to review, camera movement to confirm, faces to name, clips without a description, selects not yet sent. **Show** filters the Clips tab (or opens the People tab for faces).
- **Review progress** and **Pipeline** (found, previews made, described, people checked, reviewed).
- **Day by day:** a 26-week calendar ending at the most recent date, by when clips were Shot, Added or Reviewed. Review dates are recorded from v1.10 onwards.
- **Usage breakdown:** the space used by the app's own files (previews, thumbnails, face data, catalogue), with the size of the original footage shown separately.
- **Top keywords, Scenes, Most seen people, AI describer, Shot on, Shot types.** Claude cost is recorded per clip from v1.10 onwards.
- **All projects** adds a table of every project; click a row to open it.
