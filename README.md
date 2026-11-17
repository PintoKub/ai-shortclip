# AI ShortClip

Make **Thai 2D cartoon story shorts** (1080×1920, 30 fps, 50–70 s) with an AI pipeline and a web dashboard.
You bring your own Claude key; everything else runs on your machine.

> ระบบผลิตคลิปสั้นการ์ตูน 2D เล่าเรื่องภาษาไทย ใช้ API key ของคุณเอง ตัดสินใจเองที่ 2 จุด (อนุมัติบท / อนุมัติคลิปสุดท้าย)

```
idea → plan (concepts → bible) → writer → [you approve script] → voice + karaoke subtitles + mix
     → director (shots) → animator (Claude Code draws a HyperFrames composition) → render → QA
     → [you approve the final clip] → cover → post to YouTube / TikTok / Instagram (you click post)
```

## What you need

| Thing | Why | How |
|---|---|---|
| **Your own Claude access** | writer, planner, animator, cover ideas | an `ANTHROPIC_API_KEY`, **or** any Anthropic-compatible gateway, **or** `claude login` (Pro/Max) |
| Python 3.11+ | pipeline + dashboard | |
| Node 22+ | `npx hyperframes` renders the video | |
| ffmpeg + ffprobe | audio mix, mux, loudness check | `apt install ffmpeg` / `brew install ffmpeg` |
| Claude Code CLI | the animator runs `claude -p` | `npm i -g @anthropic-ai/claude-code` |
| Chromium | rendering / covers | `playwright install chromium` |

Your key is only sent to the API you configure. Voice (Edge TTS), subtitle timing (faster-whisper) and mixing are
free and need no key.

## Quick start

```bash
git clone https://github.com/<you>/ai-shortclip && cd ai-shortclip
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
npm i -g @anthropic-ai/claude-code

cp .env.example .env          # put YOUR key in .env (git-ignored)
python3 dashboard/server.py   # http://127.0.0.1:8099 — first run prints a generated password
```

Or with Docker:

```bash
cp .env.example .env && docker compose up --build     # then open http://127.0.0.1:8099
```

The dashboard password is generated on first start and saved in `.dashboard_pass` (Docker: `db/.dashboard_pass`).

## Your key (`.env`)

```bash
ANTHROPIC_API_KEY=sk-ant-...                 # Anthropic API
# or a gateway/proxy that speaks the Anthropic API:
ANTHROPIC_BASE_URL=https://your-gateway
ANTHROPIC_AUTH_TOKEN=your-gateway-key
```

If you use a gateway, put the model ids it exposes into `config/models.json` (`choices` + per-role defaults).
By default roles use the `sonnet` alias; pick `opus` / `haiku` per role in the dashboard (⑥ Settings) or with
`python3 orchestrator.py models --set animator=opus`.

## Make an episode

From the dashboard: ① story → ② series → ③ make episode, or from the CLI:

```bash
python3 orchestrator.py new --series my-show --ep 1 --idea "a student finds a door that was not there yesterday"
python3 orchestrator.py status                       # job id + which gate is waiting
python3 orchestrator.py advance 1 --approve          # gate 1: script OK  → voice, shots, animation, render
python3 orchestrator.py advance 1 --approve          # gate 2: final clip OK
```

Rough cost with your key (measured on Sonnet): script ≈ $0.3–0.8, animator pass ≈ $1.2 per episode; voice,
subtitles, mix, render and QA are free. The dashboard shows spend per episode.

Everything lives under `projects/<series>/ep<N>/` (git-ignored): `script.json`, `shots.json`,
`audio/`, `composition/index.html`, `renders/final.mp4`.

## Voices

- **Edge TTS** (default, no key): `th-TH-PremwadeeNeural`, `th-TH-NiwatNeural`.
  ⚠ edge-tts uses Microsoft's Read Aloud endpoint and has **no documented commercial licence**. For monetised work use
  paid Azure Speech or another licensed TTS.
- **Wayu** (optional, local Thai TTS, CC-BY-NC = **non-commercial**): shown only if installed at `$WAYU_DIR`
  (default `~/.local/share/wayu-tts`). Not bundled.

## Posting (optional)

YouTube / TikTok / Instagram posting is built in, but **you** click every post and bring your own platform app
credentials. Setup guide: [`docs/PUBLISH_SETUP.md`](docs/PUBLISH_SETUP.md). Set `public_base` (must be https for
TikTok/Instagram) and `contact_email` in `config/publish.json`. Tokens are stored in `secrets/` (git-ignored, 0600).
Respect each platform's policy on AI/repetitious content.

## Repo map

- `orchestrator.py` — state machine / CLI; `shortclip/` — pipeline, planning, publishing, validation
- `dashboard/` — stdlib HTTP server + vanilla JS UI (password login, per-IP rate limit, CSRF header)
- `tools/` — TTS + karaoke + mix (`tts_natural.py`), retime, finalize/QA, selftests
- `agents/` — writer and planner prompts; `CLAUDE.md` — rules the animator follows
- `assets/sfx/` — bundled sound effects; `examples/` — a finished reference episode (also a test fixture)

## Tests

```bash
python3 -m unittest discover -s tests          # free, no network, no key
```

## Security notes

- Never commit `.env`, `secrets/`, `.dashboard_pass` (all git-ignored).
- The dashboard binds to `127.0.0.1`. If you expose it, put it behind HTTPS (the session cookie is `Secure`).
- Claude Code runs with `Read,Write,Edit,Bash,Glob,Grep` inside the episode folder to draw the composition — run it on a
  machine/container you are comfortable with.

## License

MIT (code). Fonts (Kanit, Sarabun) are SIL OFL. Edge TTS / Wayu voices have their own terms — see above.
