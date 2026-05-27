// Participant registration (mockup-style UI).
// UI: 3 multi-select rows (lang/format/role) + experience slider + 2 inputs.
// Mapping to backend values:
//   language:  both selected → "DE/EN",  EN only → "EN",  DE only → "DE",  none → "DE/EN"
//   format:    both selected → "egal",   OPD only → "OPD", BP only → "BP", none → "egal"
//   role:      both selected → "SJ",     S only → "S",     J only → "J",   none → "SJ"
//   experience: 1 (Beginner) / 2 (Intermediate) / 3 (Advanced)
(() => {
  const params = new URLSearchParams(window.location.search);
  const eventCode = params.get("event");
  const formEl = document.getElementById("form");
  const submitEl = document.getElementById("submit");
  const statusEl = document.getElementById("status");
  const codeEl = document.getElementById("event-code");
  const countdownEl = document.getElementById("countdown");

  if (!eventCode || !/^\d{9}$/.test(eventCode)) {
    statusEl.textContent = "Missing or invalid event code in the URL.";
    formEl.style.display = "none";
    return;
  }
  codeEl.textContent = `#${eventCode}`;

  const TOKEN_KEY = `bt_${eventCode}`;
  const DRAFT_KEY = `form_draft_${eventCode}`;

  const existingToken = localStorage.getItem(TOKEN_KEY);
  const isModify = params.get("modify") === "1" && !!existingToken;

  // Already registered? Skip the form — unless they came via the "modify"
  // link on /waiting, in which case we'll preload their current values.
  if (existingToken && !isModify) {
    window.location.replace(`/waiting?event=${eventCode}`);
    return;
  }

  // ───── Multi-select row logic (rows 1-3 in the mockup) ────────────────
  const multiRows = formEl.querySelectorAll(".row-1-3");
  multiRows.forEach((row) => {
    const buttons = row.querySelectorAll("button");
    buttons.forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        const sel = btn.getAttribute("data-selected") === "true";
        btn.setAttribute("data-selected", !sel);
        repaintRow(row, buttons);
        saveDraft();
      });
    });
  });

  function repaintRow(row, buttons) {
    let selected = 0;
    buttons.forEach((b) => {
      if (b.getAttribute("data-selected") === "true") selected++;
    });
    buttons.forEach((btn) => {
      const sel = btn.getAttribute("data-selected") === "true";
      if (sel) {
        btn.classList.remove("bg-black/[0.07]", "text-black/40", "font-geist-mono-regular");
        btn.classList.add("bg-[#FFD200]", "text-[#C76200]", "font-geist-mono-bold");
      } else {
        btn.classList.remove("bg-[#FFD200]", "text-[#C76200]", "font-geist-mono-bold");
        btn.classList.add("bg-black/[0.07]", "text-black/40", "font-geist-mono-regular");
      }
      btn.style.flex = "";
      if (selected === 1) {
        btn.style.flex = sel ? "3" : "2";
      } else {
        btn.style.flex = "1";
      }
    });
  }

  function rowValue(field) {
    // Returns the backend-mapped value for a row, or null if nothing's
    // selected (which submit() treats as invalid).
    const row = formEl.querySelector(`.row-1-3[data-field="${field}"]`);
    if (!row) return null;
    const selected = Array.from(row.querySelectorAll('button[data-selected="true"]'))
      .map((b) => b.dataset.value);
    if (selected.length === 0) return null;
    if (field === "language") return selected.length === 2 ? "DE/EN" : selected[0];
    if (field === "format")   return selected.length === 2 ? "egal"  : selected[0];
    if (field === "role")     return selected.length === 2 ? "SJ"    : selected[0];
    return null;
  }

  // ───── Experience slider (single-select with toggleable highlight) ────
  const slider = document.getElementById("level-slider");
  const sliderHighlight = document.getElementById("slider-highlight");
  const sliderButtons = slider.querySelectorAll("button");

  function setExperience(value /* 1|2|3 */) {
    sliderButtons.forEach((b) => {
      const isActive = String(b.dataset.value) === String(value);
      b.classList.toggle("text-[#C76200]", isActive);
      b.classList.toggle("font-geist-mono-bold", isActive);
      b.classList.toggle("text-black/40", !isActive);
      b.classList.toggle("font-geist-mono-regular", !isActive);
    });
    if (value == null) {
      sliderHighlight.style.opacity = "0";
      return;
    }
    sliderHighlight.style.opacity = "1";
    const idx = value - 1;
    if (idx === 0)      sliderHighlight.style.left = "4px";
    else if (idx === 1) sliderHighlight.style.left = "calc(4px + (100% - 8px) / 3)";
    else                sliderHighlight.style.left = "calc(4px + 2 * (100% - 8px) / 3)";
    slider.dataset.value = String(value);
  }

  function getExperience() {
    const v = parseInt(slider.dataset.value || "0", 10);
    return [1, 2, 3].includes(v) ? v : null;
  }

  sliderButtons.forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const v = parseInt(btn.dataset.value, 10);
      setExperience(v);
      saveDraft();
    });
  });
  // No default — user must explicitly pick an experience level.

  // ───── Forced-judge slider (Yes/No, mirrors the experience slider) ────
  const fjSlider = document.getElementById("forced-judge-slider");
  const fjHighlight = document.getElementById("forced-judge-highlight");
  const fjButtons = fjSlider.querySelectorAll("button");

  function setForcedJudge(value /* 0|1 */) {
    fjButtons.forEach((b) => {
      const isActive = String(b.dataset.value) === String(value);
      b.classList.toggle("text-[#C76200]", isActive);
      b.classList.toggle("font-geist-mono-bold", isActive);
      b.classList.toggle("text-black/40", !isActive);
      b.classList.toggle("font-geist-mono-regular", !isActive);
    });
    if (value == null) {
      fjHighlight.style.opacity = "0";
      return;
    }
    fjHighlight.style.opacity = "1";
    fjHighlight.style.left = String(value) === "0"
      ? "4px"
      : "calc(4px + (100% - 8px) / 2)";
    fjSlider.dataset.value = String(value);
  }

  function getForcedJudge() {
    // Default to "No" (false) when nothing has been selected yet.
    const v = fjSlider.dataset.value;
    return v === "1";
  }

  fjButtons.forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      setForcedJudge(parseInt(btn.dataset.value, 10));
      saveDraft();
    });
  });

  // ───── Free-text inputs ───────────────────────────────────────────────
  const nameInput = formEl.querySelector('input[name="name"]');
  const reqInput  = formEl.querySelector('input[name="special_request"]');
  [nameInput, reqInput].forEach((el) => el.addEventListener("input", saveDraft));

  // ───── Selection-apply helper (used by draft restore + modify prefill) ─
  function applySelection(field, picks) {
    const row = formEl.querySelector(`.row-1-3[data-field="${field}"]`);
    if (!row) return;
    const buttons = row.querySelectorAll("button");
    buttons.forEach((b) => {
      b.setAttribute("data-selected", picks.includes(b.dataset.value) ? "true" : "false");
    });
    repaintRow(row, buttons);
  }
  function backendValueToPicks(field, value) {
    if (field === "language") return value === "DE/EN" ? ["DE", "EN"] : [value];
    if (field === "format")   return value === "egal"  ? ["BP", "OPD"] : [value];
    if (field === "role")     return value === "SJ"    ? ["S", "J"]    : [value];
    return [];
  }

  // ───── Draft persistence ──────────────────────────────────────────────
  function readSelectedValues(field) {
    const row = formEl.querySelector(`.row-1-3[data-field="${field}"]`);
    return Array.from(row.querySelectorAll('button[data-selected="true"]'))
      .map((b) => b.dataset.value);
  }
  function saveDraft() {
    const draft = {
      name: nameInput.value,
      language_sel: readSelectedValues("language"),
      format_sel: readSelectedValues("format"),
      role_sel: readSelectedValues("role"),
      experience: getExperience(),
      special_request: reqInput.value,
      forced_judge_last: getForcedJudge(),
    };
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  }
  function restoreDraft() {
    let draft;
    try { draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null"); } catch (_) {}
    if (!draft) return;
    if (typeof draft.name === "string") nameInput.value = draft.name;
    if (typeof draft.special_request === "string") reqInput.value = draft.special_request;
    if (Number.isInteger(draft.experience)) setExperience(draft.experience);
    if (typeof draft.forced_judge_last === "boolean") {
      setForcedJudge(draft.forced_judge_last ? 1 : 0);
    }
    if (Array.isArray(draft.language_sel)) applySelection("language", draft.language_sel);
    if (Array.isArray(draft.format_sel))   applySelection("format",   draft.format_sel);
    if (Array.isArray(draft.role_sel))     applySelection("role",     draft.role_sel);
  }

  // ───── Modify mode: prefill from /me (overrides any local draft) ─────
  async function prefillFromServer() {
    try {
      const r = await fetch(
        `/api/events/${eventCode}/participants/me?token=${existingToken}`,
        { cache: "no-store" }
      );
      if (!r.ok) return;
      const data = await r.json();
      const p = data.participant;
      nameInput.value = p.name || "";
      reqInput.value  = p.special_request || "";
      setExperience(parseInt(p.experience, 10) || 1);
      setForcedJudge(p.forced_judge_last ? 1 : 0);
      applySelection("language", backendValueToPicks("language", p.language));
      applySelection("format",   backendValueToPicks("format",   p.format));
      applySelection("role",     backendValueToPicks("role",     p.role));
      saveDraft();
    } catch (_) {}
  }

  if (isModify) {
    // Reflect the intent in the page title so the user knows they're not
    // creating a new entry.
    const h1 = document.querySelector("h1");
    if (h1) h1.textContent = "Modify";
    prefillFromServer();
  } else {
    restoreDraft();
  }

  // ───── Countdown ──────────────────────────────────────────────────────
  let deadlineMs = null;
  async function fetchDeadline() {
    try {
      const r = await fetch(`/api/events/${eventCode}/public`, { cache: "no-store" });
      if (!r.ok) return;
      const data = await r.json();
      deadlineMs = data.reg_deadline ? data.reg_deadline * 1000 : null;
    } catch (_) {}
  }
  function tickCountdown() {
    if (deadlineMs == null) { countdownEl.textContent = " "; return; }
    const ms = deadlineMs - Date.now();
    if (ms <= 0) { countdownEl.textContent = "time's up"; return; }
    const s = Math.floor(ms / 1000);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    countdownEl.textContent = h > 0
      ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")} left`
      : `${m}:${String(r).padStart(2, "0")} left`;
  }
  fetchDeadline().then(tickCountdown);
  setInterval(tickCountdown, 1000);
  setInterval(fetchDeadline, 30_000); // refresh in case admin pushes the deadline

  // ───── Validation feedback ───────────────────────────────────────────
  // For inputs and the slider we ring the element itself (already rounded).
  // For multi-select rows we ring each *button* — ringing the row container
  // would draw one rectangle around both buttons (it's wider than the
  // buttons because of the gap) which looked broken.
  function markInvalid(el) {
    if (!el) return;
    if (el.classList.contains("row-1-3")) {
      el.querySelectorAll("button").forEach((b) =>
        b.classList.add("ring-2", "ring-red-400"));
    } else {
      el.classList.add("ring-2", "ring-red-400");
    }
  }
  function clearInvalid() {
    formEl.querySelectorAll(".ring-red-400").forEach((el) => {
      el.classList.remove("ring-2", "ring-red-400");
    });
  }
  // Whenever the user interacts with anything, drop the red highlights.
  formEl.addEventListener("click", clearInvalid, true);
  formEl.addEventListener("input", clearInvalid, true);

  // ───── Submit with retry ──────────────────────────────────────────────
  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearInvalid();

    const name = nameInput.value.trim();
    const lang = rowValue("language");
    const fmt  = rowValue("format");
    const role = rowValue("role");
    const exp  = getExperience();

    const missing = [];
    if (!name) missing.push({ el: nameInput,                                   label: "your name" });
    if (!lang) missing.push({ el: formEl.querySelector('[data-field="language"]'), label: "a language" });
    if (!fmt)  missing.push({ el: formEl.querySelector('[data-field="format"]'),   label: "a format" });
    if (!role) missing.push({ el: formEl.querySelector('[data-field="role"]'),     label: "a role (speaker / judge)" });
    if (!exp)  missing.push({ el: document.getElementById("level-slider"),         label: "an experience level" });

    if (missing.length > 0) {
      missing.forEach((m) => markInvalid(m.el));
      statusEl.textContent = `please pick ${missing.map((m) => m.label).join(", ")}`;
      missing[0].el.scrollIntoView({ behavior: "smooth", block: "center" });
      if (!name) nameInput.focus();
      return;
    }
    submitEl.disabled = true;
    submitEl.classList.add("opacity-50");
    statusEl.textContent = "submitting…";

    // Modify uses the existing token so the backend updates the same row.
    // First registration generates a fresh one.
    const browserToken = existingToken || randomToken();
    const body = {
      browser_token: browserToken,
      name,
      language: rowValue("language"),
      format: rowValue("format"),
      role: rowValue("role"),
      experience: getExperience(),
      could_speak_last: true,
      special_request: reqInput.value.trim() || null,
      // If the participant explicitly picked Judge-only, they're opting in
      // to judging — the "forced last time" fairness flag doesn't apply.
      forced_judge_last: role === "J" ? false : getForcedJudge(),
    };

    const MAX_ATTEMPTS = 5;
    let attempt = 0, delay = 1000;
    while (attempt < MAX_ATTEMPTS) {
      try {
        const r = await fetch(`/api/events/${eventCode}/participants`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (r.ok) {
          localStorage.setItem(TOKEN_KEY, browserToken);
          localStorage.removeItem(DRAFT_KEY);
          window.location.replace(`/waiting?event=${eventCode}`);
          return;
        }
        if (r.status >= 400 && r.status < 500) {
          let detail = `HTTP ${r.status}`;
          try { const j = await r.json(); detail = j.detail || detail; } catch (_) {}
          statusEl.textContent = detail;
          submitEl.disabled = false;
          submitEl.classList.remove("opacity-50");
          return;
        }
        throw new Error(`HTTP ${r.status}`);
      } catch (err) {
        attempt += 1;
        if (attempt >= MAX_ATTEMPTS) {
          statusEl.textContent = "couldn't reach the server — tap Submit again";
          submitEl.disabled = false;
          submitEl.classList.remove("opacity-50");
          return;
        }
        statusEl.textContent = `retrying… (${attempt}/${MAX_ATTEMPTS})`;
        await new Promise((res) => setTimeout(res, delay));
        delay *= 2;
      }
    }
  });

  function randomToken() {
    if (window.crypto?.randomUUID) {
      return crypto.randomUUID().replaceAll("-", "");
    }
    const arr = new Uint8Array(16);
    crypto.getRandomValues(arr);
    return Array.from(arr, (b) => b.toString(16).padStart(2, "0")).join("");
  }
})();

// Best-effort service worker.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
