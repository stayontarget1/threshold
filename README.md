# THRESHOLD

A private, minimalist alert board. It stays **quiet** unless something in the last
24 hours crossed the line on **surveillance, government overreach, AI threats, or
consumer privacy**. When it isn't quiet, you get a tight list: headline, date,
source, direct link, and a tier.

Companion to *The Signal* — separate repo, separate purpose. The Signal reads;
THRESHOLD flags.

## How it works

```
feeds (RSS)  ──►  judge.py  ──►  data/feed.json  ──►  index.html
                  (the brain)     (the data)          (the face)
```

1. **GitHub Actions** runs `scripts/judge.py` on a schedule (hourly by default).
2. The script pulls the feeds in `scripts/feeds.txt`, keeps only the last 24h,
   drops anything already seen, and runs a **free keyword pre-filter**.
3. Survivors are sent to an **LLM judge** in batches. The rubric (in `judge.py`)
   encodes the Tier S / A / B / KILL rules. Most stories are KILLed.
4. The kept items are written to `data/feed.json` and committed. GitHub Pages
   serves the updated page automatically.

### The tiers

- **S** — drop everything: named villain **+** (leak/FOIA/court filing **or**
  surveillance→real-world enforcement) **+** universal victim pool.
- **A** — clear villain, deployed system, broad pool, credible source.
- **B** — real but narrower / weaker sourcing.
- **KILL** — op-eds, mere proposals, narrow-subset threats, "good news" arcs,
  hard-partisan stories. Never shown.

Edit the `RUBRIC` string in `scripts/judge.py` to tune your editorial taste, and
edit `scripts/feeds.txt` to change sources.

## Setup (one time)

1. **Create a GitHub repo** and push these files.
2. **Settings → Pages** → deploy from branch `main`, root. Your site:
   `https://<you>.github.io/<repo>/`.
3. **Settings → Secrets and variables → Actions:**
   - Add a secret `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`).
   - If using OpenAI, add a *variable* `JUDGE_PROVIDER` = `openai`.
4. **Settings → Actions → General →** Workflow permissions → **Read and write**.
5. **Actions tab →** run **"THRESHOLD scan"** once via *Run workflow* to populate
   real data. After that it runs hourly on its own.

## Cost

You pay per *new story judged*, not per cron run — every URL is judged once and
cached (`data/seen.json`), and a free pre-filter removes most noise first. So
hourly ≈ a few-times-a-day in cost. Realistically **~$1–3/month** with a cheap
model (Claude Haiku / GPT-4o-mini class). To run with **no API at all**, set
`JUDGE_PROVIDER=none` for a blunt keyword-only mode.

## Run locally

```bash
pip install -r scripts/requirements.txt
export ANTHROPIC_API_KEY=sk-...      # or OPENAI_API_KEY + JUDGE_PROVIDER=openai
python scripts/judge.py              # rewrites data/feed.json
python -m http.server 8000           # then open http://localhost:8000
```

The repo ships with **sample data** in `data/feed.json` so the page looks alive
before the first real run.
