// Waiting page: fixed "Done :)" header, countdown, live count,
// event-code card with QR + Share. Redirects to /rooms when admin publishes.
(() => {
  const params = new URLSearchParams(window.location.search);
  const eventCode = params.get("event");

  const countEl    = document.getElementById("count");
  const codeEl     = document.getElementById("event-code-display");
  const countdownEl = document.getElementById("countdown");
  const modifySuffixEl = document.getElementById("modify-suffix");
  const modifyLink = document.getElementById("modify-link");
  const unregisterBtn = document.getElementById("unregister-btn");
  const qrBtn      = document.getElementById("qr-btn");
  const shareBtn   = document.getElementById("share-btn");
  const modal      = document.getElementById("qr-modal");
  const qrImg      = document.getElementById("qr-img");
  const qrUrl      = document.getElementById("qr-url");
  const qrCloseBtn = document.getElementById("qr-close");

  if (!eventCode || !/^\d{9}$/.test(eventCode)) {
    return;
  }
  codeEl.textContent = `#${eventCode}`;

  const participantUrl = `${location.origin}/register?event=${eventCode}`;
  modifyLink.href = `/register?event=${eventCode}&modify=1`;

  const TOKEN_KEY = `bt_${eventCode}`;
  const browserToken = localStorage.getItem(TOKEN_KEY);

  // Without a browser token, the "modify your registration" link makes no
  // sense — hide it. (Previously this was tied to the format lookup too.)
  if (!browserToken) {
    modifySuffixEl.style.display = "none";
  }

  // ───── Unregister (self-service removal) ──────────────────────────────
  // Pulls the participant's own entry while registration is open. Two taps:
  // the first arms a confirm state, the second actually deletes.
  let confirmArmed = false;
  let confirmTimer = null;
  function resetUnregister() {
    confirmArmed = false;
    unregisterBtn.textContent = "unregister";
    unregisterBtn.classList.remove("text-[#C0392B]", "font-geist-medium");
  }
  if (unregisterBtn) {
    unregisterBtn.addEventListener("click", async () => {
      if (!browserToken) return;
      if (!confirmArmed) {
        confirmArmed = true;
        unregisterBtn.textContent = "tap to confirm";
        unregisterBtn.classList.add("text-[#C0392B]", "font-geist-medium");
        clearTimeout(confirmTimer);
        confirmTimer = setTimeout(resetUnregister, 4000);
        return;
      }
      clearTimeout(confirmTimer);
      unregisterBtn.disabled = true;
      unregisterBtn.textContent = "removing…";
      try {
        const r = await fetch(
          `/api/events/${eventCode}/participants/me?token=${browserToken}`,
          { method: "DELETE" }
        );
        if (r.ok || r.status === 404) {
          // Gone (or already gone): drop the local token + draft and send
          // them back to a fresh registration form.
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem(`form_draft_${eventCode}`);
          window.location.replace(`/register?event=${eventCode}`);
          return;
        }
        let detail = `HTTP ${r.status}`;
        try { const j = await r.json(); detail = j.detail || detail; } catch (_) {}
        unregisterBtn.disabled = false;
        unregisterBtn.textContent = detail;
        setTimeout(resetUnregister, 2500);
      } catch (_) {
        unregisterBtn.disabled = false;
        unregisterBtn.textContent = "failed — try again";
        setTimeout(resetUnregister, 2500);
      }
    });
  }

  // ───── Share + QR ─────────────────────────────────────────────────────
  shareBtn.addEventListener("click", async () => {
    if (navigator.share) {
      try { await navigator.share({ url: participantUrl, title: "Debate Event" }); return; } catch (_) {}
    }
    let ok = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(participantUrl);
        ok = true;
      }
    } catch (_) {}
    if (!ok) ok = copyFallback(participantUrl);
    if (ok) flashShare();
  });

  function copyFallback(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "0";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, text.length);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) {}
    document.body.removeChild(ta);
    return ok;
  }

  function flashShare() {
    shareBtn.classList.add("ring-2", "ring-emerald-400");
    setTimeout(() => shareBtn.classList.remove("ring-2", "ring-emerald-400"), 900);
  }

  function openQr() {
    qrImg.innerHTML = "";
    new QRCode(qrImg, {
      text: participantUrl, width: 240, height: 240,
      colorDark: "#191919", colorLight: "#ffffff",
      correctLevel: QRCode.CorrectLevel.M,
    });
    qrUrl.textContent = participantUrl;
    modal.classList.remove("hidden");
  }
  function closeQr() { modal.classList.add("hidden"); }
  qrBtn.addEventListener("click", openQr);
  qrCloseBtn.addEventListener("click", closeQr);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeQr(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeQr(); });

  // ───── Countdown ──────────────────────────────────────────────────────
  let deadlineMs = null;
  let regOpen = true;
  function tickCountdown() {
    if (!regOpen) {
      countdownEl.textContent = "registration closed";
      modifySuffixEl.style.display = "none";
      return;
    }
    if (deadlineMs == null) { countdownEl.textContent = ""; return; }
    const ms = deadlineMs - Date.now();
    if (ms <= 0) { countdownEl.textContent = "time's up "; return; }
    const s = Math.floor(ms / 1000);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    countdownEl.textContent = h > 0
      ? `${h}:${String(m).padStart(2,"0")}:${String(r).padStart(2,"0")} left `
      : `${m}:${String(r).padStart(2,"0")} left `;
  }
  setInterval(tickCountdown, 1000);

  // ───── Poll /public every 1s — count + status + deadline refresh ──────
  async function poll() {
    try {
      const r = await fetch(`/api/events/${eventCode}/public`, { cache: "no-store" });
      if (!r.ok) return;
      const data = await r.json();
      countEl.textContent = String(data.participant_count);
      deadlineMs = data.reg_deadline ? data.reg_deadline * 1000 : null;
      regOpen = data.status === "open";
      if (data.status === "published") {
        const url = browserToken
          ? `/rooms?event=${eventCode}&me=${browserToken}`
          : `/rooms?event=${eventCode}`;
        window.location.replace(url);
      }
    } catch (_) {}
  }
  poll();
  setInterval(poll, 1000);
})();
