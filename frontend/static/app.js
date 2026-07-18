// Chat page behaviour (§6.5). Deliberately minimal: collect a message, POST it to
// the local proxy, render whatever comes back. No formatting of figures, no
// client-side interpretation — the reply text is the pipeline's, verbatim.

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

input.focus();
