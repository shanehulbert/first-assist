Ingest Sorter looks here first for ffmpeg and ffprobe.

If you can't install ffmpeg with Homebrew (for example, no admin rights on a work Mac),
download static macOS builds of BOTH "ffmpeg" and "ffprobe" (Apple Silicon / arm64 if
your Mac has an M-series chip), unzip them, and put the two files in this folder.

Then, in Terminal, run (drag this folder into Terminal to fill in the path):
    chmod +x "<this folder>/ffmpeg" "<this folder>/ffprobe"
    xattr -d com.apple.quarantine "<this folder>/ffmpeg" "<this folder>/ffprobe"

Quit Terminal and relaunch Ingest Sorter. The red "ffmpeg isn't installed" banner should be gone.
