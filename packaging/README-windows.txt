AI ShortClip for Windows
========================

1. Unzip this folder somewhere you can write to (e.g. Documents\ShortClip). Not inside Program Files.

2. Install the outside tools once (open PowerShell):

     winget install Gyan.FFmpeg
     winget install OpenJS.NodeJS.LTS
     npm i -g @anthropic-ai/claude-code

   Close PowerShell and open a new window so the new PATH is used.

3. Double-click ShortClip.exe
   - First start creates .env  -> open it with Notepad and set ANTHROPIC_API_KEY=sk-ant-...
     (or run `claude login` once instead, if you have Claude Pro/Max)
   - The window prints the dashboard password (also saved in .dashboard_pass)
   - Your browser opens http://127.0.0.1:8099

4. Optional, for cover images:  ShortClip.exe setup   (downloads Chromium for Playwright)

Other commands (run in this folder from PowerShell / cmd):

   ShortClip.exe doctor                     check that everything is installed
   ShortClip.exe --port 9000                dashboard on another port
   ShortClip.exe orchestrator status        the orchestrator CLI (same as `python orchestrator.py ...`)

Your episodes go to projects\, the database to db\. Keep the whole folder together:
ShortClip.exe runs the .py files next to it.

Close the window (or Ctrl+C) to stop the dashboard. Jobs that were started keep running in the background
until they finish.

Thai subtitles in the first voice step download a Whisper model (~1.5 GB) on first use.
