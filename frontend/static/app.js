// Landing page + chat behaviour (§6.5). Vanilla, no imports, no external assets.
//
// The chat block is unchanged in spirit from the original page: collect a message,
// POST it to the local proxy, render whatever comes back. No formatting of figures,
// no client-side interpretation — the reply text is the pipeline's, verbatim.

/* ============================ Chat ============================ */

const log = document.getElementById("log");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const send = document.getElementById("send");

// One session per tab, so n8n's buffer-window memory resolves follow-ups such as
// "and NICE?" to the right conversation. sessionStorage (not localStorage) keeps a
// second tab a genuinely separate conversation.
const SESSION_KEY = "investment-team-session-id";
let sessionId = sessionStorage.getItem(SESSION_KEY);
if (!sessionId) {
  sessionId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
  sessionStorage.setItem(SESSION_KEY, sessionId);
}

function addMessage(text, role, extraClass) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}${extraClass ? " " + extraClass : ""}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text; // textContent, not innerHTML — the reply is untrusted text
  wrap.appendChild(bubble);
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return wrap;
}

function addPending() {
  const wrap = addMessage("Running the analysis — this takes about a minute…", "bot", "pending");
  const dots = document.createElement("span");
  dots.className = "dots";
  dots.innerHTML = "<i></i><i></i><i></i>";
  wrap.querySelector(".bubble").appendChild(dots);
  return wrap;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;

  addMessage(message, "user");
  input.value = "";
  input.disabled = true;
  send.disabled = true;
  const pending = addPending();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
    const data = await res.json().catch(() => ({}));
    pending.remove();
    const reply = data.reply || `degraded: no reply from the server (HTTP ${res.status}).`;
    addMessage(reply, "bot", data.degraded ? "degraded" : "");
  } catch (err) {
    pending.remove();
    addMessage(`degraded: ${err.message}`, "bot", "degraded");
  } finally {
    input.disabled = false;
    send.disabled = false;
    input.focus();
  }
});

// Focus the input only once it scrolls into view, so the page opens at the hero
// rather than jumping to the chat at the bottom.
const chatSection = document.getElementById("chat");
if (chatSection && "IntersectionObserver" in window) {
  let focused = false;
  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (e.isIntersecting && !focused) {
          focused = true;
          input.focus({ preventScroll: true });
          io.disconnect();
        }
      }
    },
    { threshold: 0.5 }
  );
  io.observe(chatSection);
}

/* ======================= Scroll reveal ======================= */

const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

if (!prefersReduced && "IntersectionObserver" in window) {
  const revealer = new IntersectionObserver(
    (entries, obs) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          e.target.classList.add("in");
          obs.unobserve(e.target);
        }
      }
    },
    { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
  );
  document.querySelectorAll(".reveal").forEach((el) => revealer.observe(el));
} else {
  document.querySelectorAll(".reveal").forEach((el) => el.classList.add("in"));
}

/* ================= Finance background animation ================= */
// A drifting multi-series line chart with faint candlesticks and a gridline sweep.
// Decorative only. DPR-aware, pauses when the tab is hidden or the user prefers
// reduced motion. No data leaves the browser — the series are pseudo-random walks.

(function financeBackground() {
  const canvas = document.getElementById("bg");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  // Colour tokens vary by theme; read the current text colour to blend the grid.
  const isLight = window.matchMedia("(prefers-color-scheme: light)").matches;
  const UP = isLight ? "rgba(16,161,105,0.55)" : "rgba(38,194,129,0.55)";
  const DOWN = isLight ? "rgba(214,52,74,0.5)" : "rgba(255,92,114,0.5)";
  const LINE = isLight ? "rgba(70,90,120,0.10)" : "rgba(150,180,220,0.06)";
  const SERIES_COLORS = isLight
    ? ["rgba(8,145,178,0.35)", "rgba(47,107,255,0.30)", "rgba(16,161,105,0.30)"]
    : ["rgba(34,211,238,0.35)", "rgba(47,107,255,0.30)", "rgba(38,194,129,0.28)"];

  let w = 0;
  let h = 0;
  let dpr = 1;

  // Multiple line series, each a slowly-scrolling random walk of y-values in [0,1].
  const SERIES_COUNT = SERIES_COLORS.length;
  const POINTS = 90;
  const series = [];
  // Candlesticks: a sparse row of OHLC-ish bars scrolling left.
  let candles = [];
  const CANDLE_W = 12;
  const CANDLE_GAP = 26;

  function seedSeries() {
    series.length = 0;
    for (let s = 0; s < SERIES_COUNT; s++) {
      const arr = [];
      let v = 0.35 + Math.random() * 0.3;
      for (let i = 0; i < POINTS; i++) {
        v += (Math.random() - 0.5) * 0.06;
        v = Math.max(0.08, Math.min(0.92, v));
        arr.push(v);
      }
      series.push({ pts: arr, offset: 0, base: 0.2 + s * 0.24 });
    }
  }

  function seedCandles() {
    candles = [];
    if (!w) return;
    const n = Math.ceil(w / (CANDLE_W + CANDLE_GAP)) + 2;
    let mid = 0.5;
    for (let i = 0; i < n; i++) {
      mid += (Math.random() - 0.5) * 0.05;
      mid = Math.max(0.2, Math.min(0.8, mid));
      const body = 0.02 + Math.random() * 0.06;
      const wick = body + 0.02 + Math.random() * 0.04;
      const up = Math.random() > 0.48;
      candles.push({ x: i * (CANDLE_W + CANDLE_GAP), mid, body, wick, up });
    }
  }

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    seedCandles();
  }

  function drawGrid() {
    ctx.strokeStyle = LINE;
    ctx.lineWidth = 1;
    const step = 64;
    ctx.beginPath();
    for (let x = (gridPhase % step); x < w; x += step) {
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
    }
    for (let y = 0; y < h; y += step) {
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
    }
    ctx.stroke();
  }

  function drawCandles() {
    const bandTop = h * 0.55;
    const bandH = h * 0.4;
    for (const c of candles) {
      const cx = c.x + candleShift;
      const midY = bandTop + (1 - c.mid) * bandH;
      const bodyH = c.body * bandH;
      const wickH = c.wick * bandH;
      ctx.strokeStyle = c.up ? UP : DOWN;
      ctx.fillStyle = c.up ? UP : DOWN;
      ctx.globalAlpha = 0.5;
      ctx.beginPath();
      ctx.moveTo(cx + CANDLE_W / 2, midY - wickH);
      ctx.lineTo(cx + CANDLE_W / 2, midY + wickH);
      ctx.stroke();
      ctx.fillRect(cx, midY - bodyH, CANDLE_W, bodyH * 2);
      ctx.globalAlpha = 1;
    }
  }

  function drawSeries() {
    for (let s = 0; s < series.length; s++) {
      const ser = series[s];
      ctx.strokeStyle = SERIES_COLORS[s];
      ctx.lineWidth = 2;
      ctx.beginPath();
      const span = w + 120;
      for (let i = 0; i < ser.pts.length; i++) {
        const x = (i / (ser.pts.length - 1)) * span - 60 + ser.offset;
        const y = h * 0.12 + (1 - ser.pts[i]) * h * 0.5;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
  }

  let gridPhase = 0;
  let candleShift = 0;
  let scrollAccum = 0;
  let raf = null;
  let running = false;

  function tick() {
    if (!running) return;
    ctx.clearRect(0, 0, w, h);
    drawGrid();
    drawCandles();
    drawSeries();

    gridPhase -= 0.25;
    candleShift -= 0.4;
    // Recycle the leftmost candle to the right once it scrolls off.
    if (candles.length) {
      const first = candles[0];
      if (first.x + candleShift < -(CANDLE_W + CANDLE_GAP)) {
        candles.shift();
        const last = candles[candles.length - 1];
        let mid = Math.max(0.2, Math.min(0.8, last.mid + (Math.random() - 0.5) * 0.05));
        candles.push({
          x: last.x + CANDLE_W + CANDLE_GAP,
          mid,
          body: 0.02 + Math.random() * 0.06,
          wick: 0.04 + Math.random() * 0.06,
          up: Math.random() > 0.48,
        });
      }
    }

    // Scroll the line series left, appending fresh points at a slow cadence.
    scrollAccum += 0.6;
    for (const ser of series) {
      ser.offset -= 0.6;
    }
    if (scrollAccum >= (w + 120) / (POINTS - 1)) {
      scrollAccum = 0;
      for (const ser of series) {
        ser.offset += (w + 120) / (POINTS - 1);
        const arr = ser.pts;
        let v = arr[arr.length - 1] + (Math.random() - 0.5) * 0.06;
        v = Math.max(0.08, Math.min(0.92, v));
        arr.push(v);
        arr.shift();
      }
    }

    raf = requestAnimationFrame(tick);
  }

  function start() {
    if (running) return;
    running = true;
    raf = requestAnimationFrame(tick);
  }

  function stop() {
    running = false;
    if (raf) cancelAnimationFrame(raf);
    raf = null;
  }

  // Static single frame for reduced-motion: draw once, no loop.
  function drawStatic() {
    ctx.clearRect(0, 0, w, h);
    drawGrid();
    drawCandles();
    drawSeries();
  }

  resize();
  seedSeries();
  seedCandles();

  window.addEventListener("resize", () => {
    resize();
    if (prefersReduced) drawStatic();
  });

  if (prefersReduced) {
    drawStatic();
  } else {
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop();
      else start();
    });
    start();
  }
})();
