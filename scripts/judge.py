#!/usr/bin/env python3
"""
THRESHOLD — scan feeds, judge each story against the rubric with an LLM,
and write data/feed.json (the file the website reads).

Cost control, by design:
  1. Only stories from the last WINDOW_HOURS are considered.
  2. A free keyword/source pre-filter drops most noise before any AI call.
  3. Every URL is judged exactly once, ever (data/seen.json dedup cache).
  4. Survivors are judged in BATCHES (one API call per ~12 stories).
So running hourly costs about the same as running a few times a day —
you only pay when there is genuinely something new to judge.

Env vars:
  JUDGE_PROVIDER   "anthropic" (default) | "openai" | "none" (keyword-only, no API)
  ANTHROPIC_API_KEY / OPENAI_API_KEY
  ANTHROPIC_MODEL  default claude-haiku-4-5-20251001
  OPENAI_MODEL     default gpt-4o-mini
  WINDOW_HOURS     default 24
  MAX_CANDIDATES   default 60  (hard cap on stories sent to the AI per run)
  MAX_ITEMS        default 40  (max items kept in feed.json)
"""

import os, re, sys, json, time, html, datetime as dt
import urllib.request, urllib.error

try:
    import feedparser
except ImportError:
    sys.exit("Missing dependency. Run: pip install -r scripts/requirements.txt")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
FEED_OUT = os.path.join(DATA_DIR, "feed.json")
SEEN_OUT = os.path.join(DATA_DIR, "seen.json")
FEEDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feeds.txt")

PROVIDER = os.environ.get("JUDGE_PROVIDER", "anthropic").lower()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "24"))
MAX_CANDIDATES = int(os.environ.get("MAX_CANDIDATES", "60"))
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "40"))
BATCH = 12

# --- Pre-filter vocabulary (free, runs before any AI call) -------------------
VILLAINS = [
    "ice", "immigration and customs", "border patrol", "cbp", "dhs", "homeland security",
    "fbi", "nsa", "tsa", "atf", "dea", "fusion center",
    "flock safety", "flock", "palantir", "clearview", "anduril", "neuralink",
    "axon", "ring", "amazon ring", "fog data", "venntel", "babel street",
    "23andme", "ancestry", "genetic", "genetic", "biometric", "facial recognition",
    "license plate", "alpr", "stingray", "pegasus", "nso group", "predator spyware",
]
TOPIC_TERMS = [
    "surveillance", "spyware", "facial recognition", "biometric", "license plate",
    "location data", "data broker", "warrantless", "wiretap", "geofence",
    "privacy", "data breach", "leaked", "foia", "court filing", "internal emails",
    "deportation", "raid", "watchlist", "tracking", "scraped", "phone records",
    "ai surveillance", "predictive policing", "deepfake", "monitoring",
]
PRIMARY_SOURCE = ["leak", "foia", "court filing", "internal email", "obtained by",
                  "documents show", "documents reveal", "lawsuit", "subpoena"]

# Outlets we trust to clear the source-quality bar more easily.
HIGH_SIGNAL = ["404media", "theintercept", "eff.org", "epic.org", "themarkup",
               "propublica", "reuters", "apnews", "washingtonpost", "nytimes",
               "wired", "arstechnica", "aclu.org"]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def load_feeds():
    out = []
    with open(FEEDS_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def load_seen():
    if os.path.exists(SEEN_OUT):
        try:
            return json.load(open(SEEN_OUT))
        except Exception:
            return {}
    return {}


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def entry_time(e):
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:
            return dt.datetime.fromtimestamp(time.mktime(t), dt.timezone.utc)
    return None


def source_name(feed, url):
    title = clean(feed.feed.get("title", "")) if hasattr(feed, "feed") else ""
    if title:
        return title
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1) if m else "unknown"


def prefilter(title, summary, source_url):
    """Cheap gate. Returns True if a story is worth spending an AI call on."""
    blob = (title + " " + summary).lower()
    has_villain = any(v in blob for v in VILLAINS)
    has_topic = any(t in blob for t in TOPIC_TERMS)
    # Need at least a topic hit; a villain hit alone also qualifies.
    return has_villain or (has_topic and len(title) > 0)


# --- The rubric: this is the editorial brain ---------------------------------
RUBRIC = """You are the editor of THRESHOLD, a private alert board. You judge whether a
news story is alarming enough to surface. You are extremely selective. Most stories are KILLED.

Assign each story exactly one tier:

TIER S — "drop everything". Requires ALL of:
  - a clearly named villain (ICE, CBP, DHS, FBI, NSA, TSA, Border Patrol, Flock, Palantir,
    Clearview, Anduril, Neuralink, Axon, a major data broker, a 23andMe-type genetic firm, etc.), AND
  - EITHER a primary-source exposure (leak, FOIA release, internal emails, court filing)
    OR a digital-to-physical bridge (surveillance that led to an arrest, raid, deportation, denied entry), AND
  - a universal victim pool (anyone with a phone / a face / a car / a major-platform account).

TIER A — clear and broad. A named villain + a real DEPLOYED system + a broad victim pool,
  from a credible source, but missing one Tier-S element.

TIER B — real and on the radar, but narrower pool OR weaker sourcing OR no named Tier-S agency.

KILL — do not surface. KILL if ANY of these apply:
  - op-ed, think piece, explainer, or 30,000-foot trend summary
  - a policy PROPOSAL or possible legislation with no system actually deployed
  - the threat only affects a narrow subset (crypto investors, one company's customers,
    Tesla owners, dating-app users, attendees of one event)
  - a "good news" arc: threat resolved, hackers caught, vulnerability patched, lawsuit won by the good guys
  - strong partisan gravity that would split a general audience along political lines
  - not actually about surveillance, government overreach, AI threats, or consumer privacy

Source quality matters: primary documents and investigative outlets (404 Media, The Intercept,
EFF, EPIC, The Markup, ProPublica, Reuters, AP) clear the bar more easily than blogs or aggregators.

Return ONLY a JSON array, one object per story, in the same order, like:
[{"i":0,"tier":"S","villain":"Flock / ICE","tags":["LEAK","DIGITAL→PHYSICAL","ANYONE WHO DRIVES"],
  "why":"one short sentence on why it earned this tier"}, ...]
Use tier "KILL" for anything that should not appear. Keep "tags" to 1-3 short uppercase labels.
Keep "why" under 18 words. Output the JSON array and nothing else."""


def build_user_msg(batch):
    lines = []
    for idx, s in enumerate(batch):
        lines.append(
            f'[{idx}] TITLE: {s["title"]}\n'
            f'    SOURCE: {s["source"]}\n'
            f'    SUMMARY: {s["summary"][:280]}'
        )
    return "Judge these stories:\n\n" + "\n\n".join(lines)


def call_anthropic(system, user):
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1500,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        })
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return "".join(b.get("text", "") for b in data.get("content", []))


def call_openai(system, user):
    body = json.dumps({
        "model": OPENAI_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={
            "Authorization": "Bearer " + OPENAI_KEY,
            "content-type": "application/json",
        })
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return data["choices"][0]["message"]["content"]


def parse_verdicts(text):
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except Exception:
        return []


def judge_batch(batch):
    user = build_user_msg(batch)
    try:
        if PROVIDER == "anthropic" and ANTHROPIC_KEY:
            raw = call_anthropic(RUBRIC, user)
        elif PROVIDER == "openai" and OPENAI_KEY:
            raw = call_openai(RUBRIC, user)
        else:
            return None  # no provider configured
        return parse_verdicts(raw)
    except urllib.error.HTTPError as e:
        log("API HTTP error:", e.code, e.read()[:300])
    except Exception as e:
        log("API error:", repr(e))
    return None


def main():
    feeds = load_feeds()
    seen = load_seen()
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=WINDOW_HOURS)

    candidates = []
    for url in feeds:
        try:
            parsed = feedparser.parse(url)
        except Exception as e:
            log("feed failed:", url, repr(e))
            continue
        src = source_name(parsed, url)
        for e in parsed.entries:
            link = e.get("link", "")
            if not link or link in seen:
                continue
            when = entry_time(e)
            if when and when < cutoff:
                continue
            title = clean(e.get("title", ""))
            summary = clean(e.get("summary", e.get("description", "")))
            if not title:
                continue
            if not prefilter(title, summary, url):
                continue
            candidates.append({
                "title": title, "summary": summary, "url": link,
                "source": src, "date": (when or now).isoformat(),
            })

    log(f"{len(candidates)} candidates after pre-filter")
    candidates = candidates[:MAX_CANDIDATES]

    kept = []
    used_ai = False
    for start in range(0, len(candidates), BATCH):
        batch = candidates[start:start + BATCH]
        verdicts = judge_batch(batch)
        if verdicts is None:
            # No AI available — fall back to a blunt keyword heuristic so the
            # pipeline still produces output (clearly weaker than the LLM judge).
            for s in batch:
                blob = (s["title"] + " " + s["summary"]).lower()
                if not any(v in blob for v in VILLAINS):
                    continue
                tier = "A" if any(p in blob for p in PRIMARY_SOURCE) else "B"
                kept.append({**s, "tier": tier, "villain": "", "tags": ["KEYWORD"],
                             "why": "matched by keyword fallback (no AI judge configured)"})
            continue
        used_ai = True
        by_i = {v.get("i"): v for v in verdicts if isinstance(v, dict)}
        for idx, s in enumerate(batch):
            v = by_i.get(idx, {})
            tier = (v.get("tier") or "KILL").upper()
            if tier not in ("S", "A", "B"):
                continue
            kept.append({
                "tier": tier,
                "headline": s["title"],
                "url": s["url"],
                "source": s["source"],
                "date": s["date"],
                "villain": v.get("villain", ""),
                "tags": v.get("tags", [])[:3],
                "why": v.get("why", ""),
            })
        for s in batch:  # mark whole batch seen so we never re-judge
            seen[s["url"]] = now.isoformat()

    # Normalize keyword-fallback items into the output shape.
    for k in kept:
        if "headline" not in k:
            k["headline"] = k.pop("title")
            k.pop("summary", None)
        k.setdefault("villain", "")
        k.setdefault("tags", [])
        k.setdefault("why", "")

    # Merge with still-fresh items from the previous run.
    existing = []
    if os.path.exists(FEED_OUT):
        try:
            prev = json.load(open(FEED_OUT))
            for it in prev.get("items", []):
                if it.get("note") or "headline" not in it:
                    continue
                d = it.get("date")
                if d and dt.datetime.fromisoformat(d.replace("Z", "+00:00")) >= cutoff:
                    existing.append(it)
        except Exception:
            pass

    by_url = {}
    for it in existing + kept:
        by_url[it["url"]] = it
    items = list(by_url.values())

    rank = {"S": 0, "A": 1, "B": 2}
    items.sort(key=lambda x: (rank.get(x["tier"], 9), x.get("date", "")), reverse=False)
    items = items[:MAX_ITEMS]

    # Prune the seen cache to ~14 days so it doesn't grow forever.
    keep_after = (now - dt.timedelta(days=14)).isoformat()
    seen = {u: t for u, t in seen.items() if t >= keep_after}

    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump({"updated": now.isoformat(), "items": items},
              open(FEED_OUT, "w"), indent=2, ensure_ascii=False)
    json.dump(seen, open(SEEN_OUT, "w"))

    log(f"wrote {len(items)} items to feed.json (ai_used={used_ai})")


if __name__ == "__main__":
    main()
