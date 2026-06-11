// THRESHOLD — reads data/feed.json and renders the wire.
const TIER_RANK = { S: 0, A: 1, B: 2 };
let STATE = { items: [], filter: "all" };

async function load() {
  try {
    const res = await fetch("data/feed.json?t=" + Date.now());
    const data = await res.json();
    STATE.items = (data.items || []).filter((i) => TIER_RANK[i.tier] !== undefined);
    STATE.items.sort((a, b) => {
      if (TIER_RANK[a.tier] !== TIER_RANK[b.tier]) return TIER_RANK[a.tier] - TIER_RANK[b.tier];
      return new Date(b.date) - new Date(a.date);
    });
    setUpdated(data.updated);
    render();
  } catch (e) {
    document.getElementById("feed").innerHTML =
      '<div class="loading">no data yet — run the scanner.</div>';
  }
}

function setUpdated(iso) {
  const el = document.getElementById("updated");
  if (!iso) { el.textContent = "last scan: —"; return; }
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d) / 60000);
  const ago = mins < 1 ? "just now" : mins < 60 ? mins + "m ago" : Math.round(mins / 60) + "h ago";
  el.textContent = "last scan: " + ago;
}

function timeAgo(iso) {
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (isNaN(mins)) return "";
  if (mins < 60) return mins + "m";
  const h = Math.round(mins / 60);
  return h < 24 ? h + "h" : Math.round(h / 24) + "d";
}

function esc(s) {
  return String(s || "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function render() {
  const feed = document.getElementById("feed");
  const items = STATE.filter === "all"
    ? STATE.items
    : STATE.items.filter((i) => i.tier === STATE.filter);

  if (!items.length) {
    feed.innerHTML =
      '<div class="quiet"><div class="big">— quiet —</div>' +
      '<div class="sub">nothing crossed the line in the last 24 hours.</div></div>';
    document.getElementById("count").textContent = "";
    return;
  }

  feed.innerHTML = items.map((i) => {
    const tags = (i.tags || []).map((t) => '<span class="tag">' + esc(t) + "</span>").join("");
    const villain = i.villain ? '<span class="villain">' + esc(i.villain) + "</span>" : "";
    const why = i.why ? '<p class="why">' + esc(i.why) + "</p>" : "";
    return (
      '<article class="item t-' + i.tier + '">' +
        '<div class="badge tier-' + i.tier + '">' + i.tier + "</div>" +
        "<div>" +
          '<h2 class="headline"><a href="' + esc(i.url) + '" target="_blank" rel="noopener noreferrer">' +
            esc(i.headline) + "</a></h2>" +
          '<div class="meta">' +
            '<span class="src">' + esc(i.source) + "</span>" +
            "<span>" + timeAgo(i.date) + " ago</span>" +
            villain +
            '<span class="tags">' + tags + "</span>" +
          "</div>" +
          why +
        "</div>" +
      "</article>"
    );
  }).join("");

  const n = items.length;
  document.getElementById("count").textContent =
    n + " flagged" + (STATE.filter !== "all" ? " · tier " + STATE.filter : "");
}

document.getElementById("filters").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  document.querySelectorAll("#filters button").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  STATE.filter = btn.dataset.tier;
  render();
});

load();
setInterval(load, 5 * 60 * 1000); // refresh view every 5 min
