# First Assist

**AI logging and selects for DaVinci Resolve.**

Sort, tag, rate and approve your footage **before** it goes into an edit. Then send the selects to DaVinci Resolve in one step: clips imported, named, marked, coloured and tagged, on a draft timeline.

- Works with the **free** version of DaVinci Resolve, and with Studio.
- Runs on your own Mac. Your footage is never uploaded, unless you choose the optional Claude describer.
- Free and open source (MIT licence).

> **Status: v1.10, early testing.** Built and tested on macOS (Apple Silicon) with DaVinci Resolve 21. It has not been tested on Windows or Linux. Please report problems under **Issues**.

## What it does

1. **Reads your footage.** Camera, date, timecode and GPS place names. It makes thumbnails and small preview copies, so browsing is fast.
2. **Describes it for you (optional).** An AI model looks at a few frames of each clip and writes a scene, title, description, keywords and shot type. It can also recognise people you've named. Both can run entirely on your Mac.
3. **Lets you review.** Play clips, set in and out points, give star ratings, approve or reject, mark A-roll or B-roll, and fix any tags.
4. **Sends your selects to Resolve.** One button in the app, then one script in Resolve. The clips arrive with their names, in/out marks, colours and metadata, on a draft timeline.

Your original files are never renamed or deleted.

---

## Install

Allow about 15 minutes. You'll paste a few commands into **Terminal** (find it with Spotlight: press Cmd-Space and type "Terminal"). Paste each command and press Return.

### 1. Download First Assist

1. On this page, click the green **Code** button, then **Download ZIP**.
2. Unzip it.
3. Make a folder to hold it, for example `Movies/First Assist`, and move the unzipped folder inside.

The app keeps its own data (projects, thumbnails, previews) in a folder called `library`, which it creates **beside** the app folder. That's why it needs a parent folder of its own:

```
Movies/First Assist/
├── first-assist-main/      ← the app (the folder you unzipped; its name may differ)
└── library/                ← created on first run: your projects and previews
```

### 2. Check Python

```
python3 --version
```

If you see a version number (3.9 or newer), go to step 3. If macOS offers to install the "command line developer tools", click **Install**, wait for it to finish, and run the command again.

### 3. Install ffmpeg (required)

ffmpeg reads the video files. The easiest way to get it is Homebrew, a free installer for Mac tools.

1. If you don't have Homebrew, install it with the one-line command at [brew.sh](https://brew.sh). At the end it prints two "Next steps" commands. Run those too.
2. Then:

```
brew install ffmpeg
```

No admin rights on your Mac? See `bin/README.txt` for a way that doesn't need them.

### 4. Install OpenCV (recommended)

This adds camera-movement detection (pan, tilt, static and so on) and people recognition.

```
pip3 install --user opencv-python-headless
```

### 5. Optional extras

| Extra | What it adds | How |
|---|---|---|
| **Ollama** | The free AI describer that runs on your Mac | See [AI describer](#ai-describer-optional) |
| **ExifTool** | Fuller camera details (aperture, ISO, lens on some cameras) | `brew install exiftool` |

### 6. Check your setup

Double-click **Check Setup.command** in the app folder. It lists what's installed and gives the fix for anything missing.

**The first time, macOS will block it** because the file came from the internet. Right-click the file, choose **Open**, then **Open** again. If there's no Open button, go to **System Settings › Privacy & Security**, scroll down and click **Open Anyway**. You only do this once per file.

### 7. Start the app

**The easy way (recommended): First Assist.app.** Build it once, then start First Assist from the Dock like any app.

1. In Terminal, run this (it takes a few seconds and only uses tools built into macOS):
   `"$HOME/Movies/First Assist/first-assist/tools/make_app.sh"`
   (change the path if your `first-assist` folder is somewhere else). It puts **First Assist.app** in your Applications folder.
2. Open **Applications** in Finder and drag **First Assist** (the purple icon) to the Dock.
3. Click it. First Assist starts in the background, with no Terminal window, and opens `http://localhost:8765` in your browser.
4. If macOS says it can't be opened the first time, open **System Settings › Privacy & Security**, scroll down and click **Open Anyway** next to First Assist, then click it in the Dock again.

- Clicking it again while First Assist is running just opens another tab; it never starts a second copy.
- To stop it, click **Quit First Assist** at the foot of the sidebar. If something is still running (scanning, AI describing), it asks first. A Send or move that has started always finishes before it stops.
- It writes a log to `library/first-assist.log`, beside the code folder.
- If you move or rename the `first-assist` folder, run `tools/make_app.sh` again.

**The backup way: Open First Assist.command.** Double-click it (right-click › **Open** the first time). A Terminal window opens and the app appears in your browser. Keep that window open while you work; close it to stop. If double-clicking won't work, drag the app folder onto the Terminal icon, then run `python3 sorter/server.py`.

---

## Your first project

Click **+ New project** and choose how the app should treat the files. The names follow the editing-room terms: managed media is organised by the app, linked media stays where it is.

| | **Managed Media** · moves selects | **Linked Media** · leaves footage in place |
|---|---|---|
| Use it for | Footage you've recently shot | Footage already filed on your drive |
| You choose | The project folder | Any number of footage folders, plus an optional folder for the edit |
| On Send | Approved clips **move** to `01_Selects` | **Nothing moves** |

A **Managed Media** project sets up this structure inside the folder you choose. Put new footage in `00_Rushes`:

```
2026-03 Birthday Party/
├── 01_Footage/
│   ├── 00_Rushes/          ← everything you shot (rejects stay here)
│   └── 01_Selects/         ← approved clips move here on Send
├── 02_Project/
│   └── _Resolve Import/    ← files written for Resolve
└── 03_Deliverables/
```

You can rename a project or switch its mode later in the **Library** panel. If you're not sure, start with **Linked Media**: it never moves a file.

**Try it on a small folder of copies first**, 10 to 20 clips, before pointing it at footage you care about.

### Review

- Click a clip to open it on the right. The **Viewer** tab gives you a large player.
- Set in and out points, rate, then approve or reject.
- **Vocabulary** is your master keyword list, shared by every project. **People** holds the people you've named.
- The **Dashboard** tab sums up a project, or all of them: what's still to do (each **Show** button takes you to those clips), review progress, a day-by-day calendar, how much space the app's own files use, top keywords, scenes, people and cameras.
- The **Metadata** panel fills Resolve's own fields. **Project details** at the top (Director, Location and so on) are added to every clip.

| Key | Action |
|---|---|
| ← → | Previous / next clip |
| Space | Play / pause |
| I / O | Set in / out at the playhead |
| P | Play from in to out |
| 1–5, 0 | Rate / clear rating |
| A / X / U | Approve / reject / unreviewed |
| B | Approve as B-roll, or switch between A-roll and B-roll |
| C | Copy the clip name |

### Send to Resolve

1. In the **Send** panel, press the Send button. This writes a script for Resolve. For Managed Media projects it also moves the selects.
2. In Resolve, open your project and click the bin you want the clips in.
3. Choose **Workspace › Scripts › First Assist - Apply Last Send**.

The script:

- imports the clips, if they aren't in the project yet
- sets each clip's name, in/out marks and colour (A-roll green, B-roll apricot)
- writes the metadata: Scene, Shot, Shot Type, Shoot Day, People, Description, Keywords, Comments, Rating, Angle, Move, plus your project details
- builds a draft timeline called "*Project name* - draft" with the selects on V1.

**Sending again is safe.** Approve more clips, press Send and run the script again. New clips go on the **end** of the same draft timeline, and names, marks and metadata are updated. The app's values win over changes made in Resolve, so do your tagging in the app and your cutting in Resolve.

The script doesn't print anything to Resolve's Console. Look in the Media Pool for the result.

**Draft timeline template (optional).** To have the draft timeline copy your own track layout and settings, name a template timeline under **Settings › Draft timeline template**.

---

## AI describer (optional)

Choose the engine under **Settings › AI describer**.

| | Local (Ollama) | Claude |
|---|---|---|
| Runs | on your Mac | Anthropic's servers (needs internet) |
| Cost | free | pay per clip, a fraction of a cent to about a cent, depending on the model |
| Privacy | nothing leaves your Mac | a few frames per clip are sent to Anthropic |
| Quality | good on what's in the shot | better on nuance |

**Local setup**

1. Install Ollama from [ollama.com](https://ollama.com) and open it.
2. Download a vision model (about 6 GB; fine on a Mac with 16 GB of memory):

```
ollama pull qwen2.5vl:7b
```

3. In the app, go to **Settings › AI describer**, choose **Local** and pick the model.

**Claude setup.** Create an API key at [platform.claude.com](https://platform.claude.com) and paste it into **Settings › AI describer**. API use is billed separately from a Claude chat subscription. The key is stored only on your Mac, in the `library` folder. The app shows an estimated cost before each run.

Then use **✦ Describe with AI** above the clips. Anything you type yourself is never overwritten.

## People recognition (optional)

Off by default. Needs OpenCV (install step 4).

1. Turn it on under **Settings › People recognition** and click **Download face models** (about 40 MB, once).
2. Click **Find people** above the clips.
3. In the **People** tab, name the faces it found, or **Reject** a face to ignore it from then on.

Named people are then tagged automatically, with a confidence light. Only confirmed or green matches are sent to Resolve. Everything stays on your Mac.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Can't be opened because it is from an unidentified developer" | Right-click the file › **Open**, or **System Settings › Privacy & Security › Open Anyway** |
| Red "ffmpeg isn't installed" banner | Do install step 3, then quit First Assist (or Terminal) and start it again |
| `python3: command not found`, or a prompt to install developer tools | Do install step 2 |
| The browser page didn't open | Go to `http://localhost:8765` yourself. If First Assist.app says the port is in use, close any First Assist Terminal window and try again. Details are in `library/first-assist.log` |
| "This page is connected to an older engine" | Quit First Assist (or Terminal) and start it again |
| The script isn't in Resolve's **Workspace › Scripts** menu | Press Send at least once. Open Resolve at least once before your first Send |
| The script ran but nothing changed | Select a bin first. Check that the footage drive is connected under the same name as when you pressed Send |
| A new feature doesn't appear after updating | Quit First Assist (or Terminal) and start it again, then refresh the browser page |
| The local AI describer is slow | The first clip loads the model, which takes a while. A smaller model is faster |

## Privacy and your data

- The app is only reachable from your own Mac (`localhost`). Nothing else on your network can open it.
- Projects, thumbnails, previews, people and face data live in the `library` folder beside the app folder. Back that folder up to keep your work. Delete it to start fresh.
- Nothing is sent anywhere, with two exceptions: a clip's GPS position is sent to OpenStreetMap to look up its place name, and the Claude describer sends frames to Anthropic if you turn it on. The face models and Ollama models are downloads to your Mac.
- Original files are never renamed or deleted. Only a **Managed Media** project's Send moves files, into `01_Selects`, and **Undo** in the Send panel puts them back.

## Known limits

- macOS only for now. The folder picker and the Resolve script install are Mac-specific.
- Whole clips only: one in/out range per clip.
- No speech transcription or dialogue search yet.
- The script matches clips by their full file path. If a drive is connected under a different name on another Mac, clips are matched by file name instead.

## Updating

Download the ZIP again and replace the app folder. Leave the `library` folder where it is: your projects are kept.

## Feedback

Please open an **Issue** on this page. It helps to include your macOS version, your Resolve version (free or Studio), what you did, what you expected, and a screenshot.

## More detail

- [docs/REFERENCE.md](docs/REFERENCE.md): detailed notes on every feature
- `resolve-tests/`: small Resolve scripts used to check what scripting can do in your version of Resolve

## Licence

MIT. See [LICENSE](LICENSE).
