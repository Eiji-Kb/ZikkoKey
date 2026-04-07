# ZikkoKey
**Enter ≠ Return** — Voice-enabled input pad for AI coding agents

ZikkoKey is a lightweight desktop input pad designed to be used alongside AI coding agents like Claude Code.
Voice input and voice-based editing are both supported. Edited text can be sent to any window.

🤔 Ugh, I pressed Enter again when I meant to press Shift+Enter. Still happens sometimes.  
On mainframe editors, the "↵" key is purely a line break — you only send to the host by pressing the Execute key (Enter). I wanted that same feel, so I built this input pad.  
Lately I've been using voice input quite a bit too, so I wired in Whisper as well.  
(This app is general-purpose, but since I mainly use Claude Code right now, it's tuned toward that.)

![English](shot-e.png)

---

## Features

- **Voice transcription** — Transcribes speech via OpenAI Whisper ※ Whisper setup is required. It is included in step 2: Python package installation (requirements.txt).
- **Voice editing** — Edit text by voice (add a line break / proofread / make it a list / change X to Y / etc. — doesn't always work perfectly; use Ctrl+Z to undo)  
  Selectable voice-editing AI — keeping costs as low as possible  
  　Claude Code CLI / Uses the Claude CLI you're already running  
  　Ollama (Qwen, etc.) / Local LLM (free) ※ Ollama must be installed separately  
  　Google Gemini API / Free tier available ※ API key required  
  　Depending on usage frequency, Google Gemini API is currently recommended — no Claude Code tokens consumed, and it's fast.  
- **Screenshot capture** — Captures the selected window. The image is saved as `shot.png` in the same folder as `zikkokey.py`. Specifying the absolute path to Claude is most reliable (e.g. `c:\zikkokey\shot.png`), but you can also add the following to Claude's `CLAUDE.md`:
    #### When zikkokey and Claude are in the same folder
    \## Screenshot  
`shot.png` in this directory is the screenshot saved by the 📸 button in the app.  
When the user mentions the screenshot or asks you to look at it, read this file.  

  #### When zikkokey and Claude are in different folders, specify the file path (e.g. `C:\zikkotest\shot.png`)

  \## Screenshot  
  The screenshot taken by the user is saved at C:\zikkotest\shot.png  
  When the user mentions the screenshot or asks you to look at it, read this file.  

- **Mute during voice input** — Temporarily mutes or lowers system audio while recording, useful when background music is playing
- **Playback of recorded audio** — Automatically plays back your recording after speaking. Handy for checking your own articulation.
- **Claude.ai usage gauge** — Displays Claude Code usage at the bottom ※ Requires `rate_limit_bridge.py` to be installed (see below)
- **New window** — Opens additional input pads. Uses less memory than running multiple instances simultaneously. (When sending to a web browser like Chrome, enabling ☑ Auto-send is recommended — text is sent without pressing the Execute key. Note: voice editing will be unavailable in this mode.)
- **Lightweight** — The main script `zikkokey.py` is under 200 KB. If you don't need the shutter sound, this single file is all you need. Easy to copy into any project folder and use from there.

## System Requirements

- Windows (GPU recommended. The Whisper voice input component can run on CPU only, but may be very slow depending on your hardware.)
  macOS should mostly work for the core features, but some parts like audio playback need Mac-specific adjustments. Since I don't currently have a Mac available, it hasn't been tested. Linux requires more changes than macOS, particularly around screen capture.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/Eiji-Kb/ZikkoKey.git
cd ZikkoKey
```

### 2. Install Python packages

```bash
pip install -r requirements.txt
```

### 3. Claude Code usage gauge (optional)

Setting up `rate_limit_bridge.py` enables the 5h / 7d rate limit bars in ZikkoKey.
Claude Code automatically calls this script and writes usage data to a cache file.

1. Copy `rate_limit_bridge.py` to `~/.zikkokey/`

2. Add the following to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python \"$HOME/.zikkokey/rate_limit_bridge.py\""
  }
}
```

### 4. Ollama (optional)

Required if you want to use a local LLM for voice editing.

1. Download and install

   Open the following URL in your browser to download the installer:  
   https://ollama.com/download/windows  
   Run OllamaSetup.exe to install.

2. Download a model

   After installation, download a model in a terminal (PowerShell or CMD), e.g.:
   ```
   ollama pull qwen3:8b
   ```
   qwen3:8b is approximately 5 GB, so it may take a few to several minutes depending on your connection speed.

3. Verify the installation

   Ollama runs in the system tray after installation. You can also verify from a terminal:
   ```
   ollama list
   ```
   If qwen3:8b appears in the list, you're ready to go.

   Note: In ZikkoKey's settings, select Ollama as the voice-editing backend and enter the downloaded model name.

---

## Launching

**From the command line:**
```bash
python zikkokey.py
```
Launching from the command line is recommended. Make sure to trust the launch directory in Claude Code.

**Windows — double-click to launch:**
```
zikkokey.vbs        # Launches without a console window (recommended)
zikkokey.bat        # Launches with a console window
```

The default UI language is English. To switch to Japanese, change it in the settings.

## How to Use

| Action | Method |
|---|---|
| Start / stop voice transcription | Hold **F4** (or hold the "Transcribe" button) |
| Start / stop AI voice editing | Hold **F5** (or hold the "Edit" button) |
| Send text to target window | **Right Ctrl** (or the "Send" button) |
| Select target window | Click "Select Window…", then click the target window |
| Lock / unlock target window | "Free / Lock" button |
| Take a screenshot | Click the 📸 button, then click the target window |
| Open settings | "Settings" button |

### Send Mode

| Mode | Behavior |
|---|---|
| Normal (fast) | Pastes all text at once |
| Slow (chunked) | Sends in small chunks. Recommended for long input to Claude Code |

Use Slow (chunked) mode to avoid paste failures when sending to Claude Code.

### Shortcuts

App-specific (custom implementation)

| Shortcut | Action |
|---|---|
| Ctrl + Z | Undo (also restores from send history when the text area is empty) |
| Ctrl + Y | Redo (re-applies history reversed by Ctrl+Z) |
| Ctrl + S | Save text to file (save dialog) |
| Ctrl + O | Load text from file (open dialog) |
| Right Ctrl | Send to target window |
| ↑ / ↓ (when empty) | Forward arrow keys to target window |
| ↑ / ↓ (while typing) | Normal cursor movement |

Tkinter Text widget standard

| Shortcut | Action |
|---|---|
| Ctrl + A | Select all |
| Ctrl + C | Copy |
| Ctrl + X | Cut |
| Ctrl + V | Paste |
| Home | Go to line start |
| End | Go to line end |
| Ctrl + Home | Go to document start |
| Ctrl + End | Go to document end |
| Shift + ←/→ | Select character by character |
| Shift + Home/End | Select to line start/end |
| Ctrl + Shift + Home/End | Select to document start/end |
| Delete | Delete character to the right of cursor |
| BackSpace | Delete character to the left of cursor |

---

Notes:
- Ctrl + Z / Ctrl + Y use a custom implementation that overrides Tkinter's default undo/redo — behavior differs due to integration with send history
- Ctrl + A normally moves to line start in Tkinter, but on Windows it typically acts as Select All

---

### Settings

![English](set-e.png)

## Acknowledgements

ZikkoKey stands on the shoulders of open-source work from around the world, starting with [OpenAI Whisper](https://github.com/openai/whisper). Heartfelt thanks to all the developers who share their work so generously.

## License

ZikkoKey v1.0 — © 2026 Tangerine Cirrus — [MIT License](LICENSE)
