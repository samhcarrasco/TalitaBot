# Setup Tutorial

A step-by-step guide to get the bot running from scratch. Takes ~20–30 minutes the
first time. For deep configuration details, see the full [README.md](README.md);
this is the fast path.

> **What it does:** logs into LinkedIn in a real browser, finds jobs matching your
> search, uses an LLM to fill out Easy Apply forms, and submits applications for you.

---

## 0. What you need first

- **Python 3.12.7** (exact version is pinned) — check with `python --version`
- **git**
- **[uv](https://github.com/astral-sh/uv)** (the package manager this project uses)
  - Install: `pip install uv` (or see the uv docs for a standalone installer)
- A **LinkedIn account** (with a complete profile — see step 6)
- An **LLM API key** — e.g. [OpenRouter](https://openrouter.ai) (easiest, many models),
  Google Gemini, OpenAI, Anthropic, or DeepSeek. Gemini/OpenRouter are cheap/often free.

---

## 1. Get the code & install

```bash
git clone https://github.com/samhcarrasco/EasyApplyBot.git
cd EasyApplyBot

uv sync                          # creates .venv and installs all dependencies
uv run playwright install chromium   # installs the browser the bot drives
```

---

## 2. Add your secrets (`.env`)

```bash
cp .env_example .env
```

Open `.env` and fill in at least:

- `linkedin_email` / `linkedin_password`
- `llm_api_key` (the key for whichever LLM you'll use in step 3)

Everything else (Telegram, Indeed, proxy) is optional — leave blank to skip.
`.env` is gitignored, so your credentials never get committed.

---

## 3. App settings (`config/app_config.py`)

Copy the example, then edit:

```bash
cp examples/config/app_config.py config/app_config.py
```

The settings you'll most likely change:

| Setting | What to set |
|---|---|
| `JOB_SITE` | `"linkedin"` (default) or `"indeed"` |
| `LLM_MODEL_TYPE` | your provider: `"openrouter"`, `"gemini"`, `"openai"`, `"claude"`, `"deepseek"`, `"ollama"` |
| `EASY_APPLY_MODEL` | the exact model id for that provider (e.g. `"google/gemini-3.1-flash-lite-preview"`) |
| `MAX_APPLIES_NUM` | how many jobs to apply to per run (LinkedIn caps Easy Apply at ~50/day) |
| `EASY_APPLY_ONLY_MODE` | `True` = only Easy Apply jobs (recommended; off-site jobs cost 10–100× more tokens) |
| `HEADLESS_MODE` | `False` to watch the browser; `True` to run hidden/faster |
| `READY_MADE_RESUME_PATH` | path to a fixed resume PDF, or `""` to auto-generate a tailored one per job |

Your API key from step 2 (`llm_api_key`) pairs with whatever `LLM_MODEL_TYPE` you pick here.

---

## 4. Search settings (`config/search_config.yaml`)

```bash
cp examples/config/search_config.yaml config/search_config.yaml
```

Edit to describe the jobs you want — `positions` is the only required field:

```yaml
positions:
  - Software engineer

remote: true
hybrid: false
onsite: false

experience_level:
  mid_senior_level: true

date:
  week: true

title_blacklist:
  - Staff
  - Principal
```

---

## 5. Your resume

The bot needs your resume as text. Create **`data/resumes/resume_text.txt`** and paste in
your full resume — include your **first name, last name, and gender** (required for the
anonymizer). The more detail you add, the better the bot answers application questions.

```bash
# see examples/data/resumes/resume_text.txt for the format
```

A `structured_resume.yaml` is also needed but is **auto-generated** from your text on first
run (or run `uv run python src/resume_builder/resume_manager.py` to generate + preview it).

**Resume PDF — two options:**
- **Auto-generate (default):** leave `READY_MADE_RESUME_PATH = ""` — a tailored resume is
  built for each job. Test it first: `uv run python src/resume_builder/resume_manager.py`
  → check `test_generated_resume.pdf` for any "No info"/"N/A" gaps, then fill those in
  `resume_text.txt`.
- **Use one fixed PDF:** put your PDF at e.g. `data/resumes/resume.pdf` and set
  `READY_MADE_RESUME_PATH = "data/resumes/resume.pdf"`.

---

## 6. Prep your LinkedIn profile (do this once, it saves money)

LinkedIn pre-fills application forms from your profile, so the more complete it is, the
fewer questions the bot pays the LLM to answer:

- Fill in experience, education, skills, contact info.
- Upload a resume under **LinkedIn → Job Preferences → Easy Apply Resume**.

---

## 7. First run & login

```bash
uv run python main.py
```

On the first run a Chromium window opens. **Log into LinkedIn manually** in that window
(handle any 2FA / captcha yourself). Your session is saved to
`browser_session/browser_state.json`, so later runs stay logged in.

Then the bot starts finding and applying to jobs, stopping at `MAX_APPLIES_NUM`.

- If it warns about missing resume fields, press **`y`** to continue or **`n`** to stop and
  add the info to `resume_text.txt` (it auto-continues after 30s).
- **Pause/resume** anytime with **`Ctrl+X`** (takes a couple seconds to react).

---

## 8. (Optional) Dashboard

A local web UI to watch runs, browse screenshots, edit config, and start/stop the bot:

```bash
uv run python dashboard.py     # then open http://127.0.0.1:8000
```

---

## 9. (Optional) Run it daily

`run_daily.cmd` (Windows) launches a single run — handy for Task Scheduler.
⚠️ **Edit the hardcoded folder path inside it** to match where you cloned the repo.

---

## Troubleshooting

- **"LLM API error" / auth failure** → check `llm_api_key` in `.env` matches the
  `LLM_MODEL_TYPE` provider, and that the model id in `EASY_APPLY_MODEL` is valid.
- **Logs in but applies to 0 jobs** → this fork already fixes the big one (LinkedIn's
  redesigned Easy Apply modal — see [the fork's commits](#about-this-fork)). If it recurs,
  LinkedIn may have changed its UI again.
- **YAML errors** → re-check indentation in `config/search_config.yaml`.
- More: see the **Troubleshooting** section in [README.md](README.md).

---

## About this fork

This is a customized fork of
[beatwad/LinkedIn-AI-Job-Applier-Ultimate](https://github.com/beatwad/LinkedIn-AI-Job-Applier-Ultimate).
Notable additions on top of upstream: a fix for LinkedIn's **new Easy Apply UI** (opens
jobs on the page layout that still serves the fillable modal), smarter job filtering and a
failure circuit-breaker, daily-limit handling, an off-site (Non-Easy-Apply) AI apply agent,
end-of-run summaries + application tracking, and Windows reliability fixes. See the git log
for the full list.
