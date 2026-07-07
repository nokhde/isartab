// Admin panel: Alpine state + click-to-place card assignment + fetch.
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) {}
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

function adminPanel(adminToken) {
  return {
    adminToken,
    event: null,
    participants: [],
    rooms: [],
    loading: false,
    loadingMsg: null,
    errorMsg: null,
    deadlineInput: "",
    countdownTick: 0,
    _interval: null,
    // Serialize mutations so responses can never apply out of order. Without
    // this, a slow PATCH could land *after* a later one and clobber its
    // state — the visible symptom was a cleared participant staying dimmed
    // (is-assigned) until manual refresh.
    _sendQueue: Promise.resolve(),

    // Set of participant IDs currently sitting in any slot. Recomputed in
    // applyState() rather than as a reactive getter — the getter form
    // returned a fresh Set each call, which Alpine's per-item :class
    // effect in the keyed <template x-for="p in participants"> did not
    // reliably re-run when only `rooms` (not `participants`) changed.
    // The visible symptom: a participant cleared from a slot stayed
    // dimmed (is-assigned) in the left list until another refresh.
    assignedIds: new Set(),

    // First-visit warning state.
    firstVisit: !localStorage.getItem(`admin_seen_${adminToken}`),
    copyState: "Copy admin link",
    copyParticipantState: "Copy",

    // Add-room dialog state.
    addRoomOpen: false,
    addRoomFormat: "BP",
    addRoomLanguage: "EN",

    // Recover-from-log dialog state.
    recoverOpen: false,
    recoverText: "",
    recoverBusy: false,
    recoverPreviewCount: 0,
    recoverPreviewNames: "",
    recoverOtherCodes: [],
    recoverResult: null,
    _recoverPreviewTimer: null,

    // Inline-rename state (only one room can be renamed at a time).
    renaming: null,
    renamingName: "",

    // Auto-proposal explanation card. Per-event dismissal flag.
    proposalDismissed: !!localStorage.getItem(`proposal_dismissed_${adminToken}`),

    // Click-to-place: an alternative to drag-and-drop that's friendlier on a
    // laptop trackpad. Click a card → it's "picked up" (marching-ants
    // highlight); click a target slot → the card flies in; if both source and
    // target hold a card, the two are swapped. selectedSlotId === null means
    // the picked-up card came from the participant pool on the left.
    selectedPid: null,
    selectedSlotId: null,

    async boot() {
      await this.refresh();
      // Re-tick the countdown once per second so it updates live.
      this._interval = setInterval(() => { this.countdownTick++; }, 1000);
    },

    // ───── HTTP helpers ──────────────────────────────────────────────────
    async refresh() {
      try {
        const r = await fetch(this.api("/state"));
        if (!r.ok) throw new Error(`Could not load state (HTTP ${r.status})`);
        this.applyState(await r.json());
      } catch (e) {
        this.errorMsg = e.message;
      }
    },

    async post(action) {
      await this.send("POST", `/${action}`);
    },

    send(method, path, body) {
      // Queue: each call waits for the previous one to finish before firing.
      // Keeps responses applied strictly in click-order. The .catch on the
      // stored handle keeps the chain alive even if one request errors.
      const run = () => this._sendImpl(method, path, body);
      const result = this._sendQueue.then(run, run);
      this._sendQueue = result.catch(() => {});
      return result;
    },

    async _sendImpl(method, path, body) {
      this.loading = true;
      this.loadingMsg = this.actionLabel(path);
      try {
        const opts = { method };
        if (body !== undefined) {
          opts.headers = { "Content-Type": "application/json" };
          opts.body = JSON.stringify(body);
        }
        const r = await fetch(this.api(path), opts);
        if (!r.ok) {
          let detail = `HTTP ${r.status}`;
          try { const j = await r.json(); detail = j.detail || detail; } catch (_) {}
          throw new Error(detail);
        }
        this.applyState(await r.json());
      } catch (e) {
        this.errorMsg = e.message;
      } finally {
        this.loading = false;
        this.loadingMsg = null;
      }
    },

    applyState(state) {
      this.event = state.event;
      // Surface participants with a special request at the top of the list —
      // they're the ones the tabmaster needs to eyeball. Array.sort is stable,
      // so the server's original order is preserved within each group.
      this.participants = [...state.participants].sort(
        (a, b) => (b.special_request ? 1 : 0) - (a.special_request ? 1 : 0)
      );
      this.rooms = state.rooms;
      const ids = new Set();
      for (const r of state.rooms)
        for (const s of r.slots)
          if (s.participant) ids.add(s.participant.id);
      this.assignedIds = ids;
      if (state.event.reg_deadline) {
        const d = new Date(state.event.reg_deadline * 1000);
        // datetime-local wants "YYYY-MM-DDTHH:MM"
        const pad = (n) => String(n).padStart(2, "0");
        this.deadlineInput =
          `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T` +
          `${pad(d.getHours())}:${pad(d.getMinutes())}`;
      }
    },

    api(path) {
      return `/api/admin/${this.adminToken}${path}`;
    },

    // ───── UI labels & helpers ───────────────────────────────────────────
    actionLabel(path) {
      if (path.includes("propose-rooms")) return "Running solver… up to 30 s";
      if (path.includes("magic-fill")) return "Magic fill… up to 30 s";
      if (path.includes("publish")) return "Publishing…";
      if (path.includes("seed-demo")) return "Seeding demo participants…";
      if (path.includes("clear-unlocked")) return "Clearing unlocked slots…";
      return "Working…";
    },

    roleLabel(r) {
      return { S: "Speaker", J: "Judge", SJ: "Sp+Jdg" }[r] || r;
    },

    langFlag(lang) {
      return { DE: "🇩🇪", EN: "🇬🇧", "DE/EN": "🇩🇪🇬🇧" }[lang] || lang;
    },

    // Region definitions used to lay out a room into a grid.
    regionsFor(format) {
      if (format === "BP") {
        return [
          { key: "og", label: "Opening Gov", role: "speaker", subroles: ["OG"] },
          { key: "cg", label: "Closing Gov", role: "speaker", subroles: ["CG"] },
          { key: "oo", label: "Opening Opp", role: "speaker", subroles: ["OO"] },
          { key: "co", label: "Closing Opp", role: "speaker", subroles: ["CO"] },
          { key: "judges", label: "Judges", role: "judge" },
        ];
      }
      if (format === "OPD") {
        return [
          { key: "gov",    label: "Government",    role: "speaker", subroles: ["Gov"] },
          { key: "opp",    label: "Opposition",    role: "speaker", subroles: ["Opp"] },
          { key: "free",   label: "Free Speakers", role: "speaker", subroles: ["Free"] },
          { key: "judges", label: "Judges",        role: "judge" },
        ];
      }
      return [];
    },

    slotsInRegion(room, region) {
      // We attach `_room` to every slot so the deeply-nested x-if can read
      // its room without relying on Alpine's nested-x-for scope chain
      // (which proved unreliable across three levels of <template>).
      return room.slots
        .filter((s) => {
          if (s.role !== region.role) return false;
          if (region.subroles && !region.subroles.includes(s.subrole)) return false;
          return true;
        })
        .map((s) => Object.assign({}, s, { _room: room }));
    },

    // True when this assigned participant has a preference (language,
    // format, or role) that this slot/room doesn't satisfy.
    isMismatch(slot, room) {
      // Defensive: if the room reference didn't reach us, bail out
      // silently instead of throwing (which would suppress the binding).
      if (!slot || !room || !slot.participant) return false;
      const p = slot.participant;
      // Language: DE/EN matches everything.
      if (p.language !== "DE/EN" && p.language !== room.language) return true;
      // Format: 'egal' matches everything.
      if (p.format !== "egal" && p.format !== room.format) return true;
      // Role: S → speaker only; J → judge only; SJ → either.
      if (p.role === "S" && slot.role === "judge") return true;
      if (p.role === "J" && slot.role === "speaker") return true;
      return false;
    },

    // Per-tag version of isMismatch: tells whether one specific dimension
    // ("language" | "format" | "role") of the participant's preference is
    // the one that doesn't fit, so we can highlight just that tag.
    tagMismatch(slot, room, dim) {
      if (!slot || !room || !slot.participant) return false;
      const p = slot.participant;
      if (dim === "language")
        return p.language !== "DE/EN" && p.language !== room.language;
      if (dim === "format")
        return p.format !== "egal" && p.format !== room.format;
      if (dim === "role") {
        if (p.role === "S" && slot.role === "judge") return true;
        if (p.role === "J" && slot.role === "speaker") return true;
      }
      return false;
    },

    roomLabelText(room) {
      return room.name && room.name.trim()
        ? room.name
        : `Room ${room.index + 1}`;
    },

    canEditMeta() {
      return this.event && this.event.status !== "published";
    },

    countdown() {
      void this.countdownTick;
      if (!this.event?.reg_deadline) return "";
      const ms = this.event.reg_deadline * 1000 - Date.now();
      if (ms <= 0) return "Time's up";
      const s = Math.floor(ms / 1000);
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const r = s % 60;
      return h > 0
        ? `${h}:${String(m).padStart(2,"0")}:${String(r).padStart(2,"0")}`
        : `${m}:${String(r).padStart(2,"0")}`;
    },

    async share() {
      const url = this.participantUrl();
      if (!url) return;
      if (navigator.share) {
        try { await navigator.share({ url, title: "Debate Event" }); return; } catch (_) {}
      }
      if (await copyText(url)) {
        this.errorMsg = "Participant link copied.";
      }
    },

    // ───── Mutations ─────────────────────────────────────────────────────
    async saveDeadline() {
      if (!this.deadlineInput) {
        await this.send("PATCH", "/event", { reg_deadline: null });
        return;
      }
      const ts = Math.floor(new Date(this.deadlineInput).getTime() / 1000);
      await this.send("PATCH", "/event", { reg_deadline: ts });
    },

    openAddRoom() {
      this.addRoomFormat = "BP";
      this.addRoomLanguage = "EN";
      this.addRoomOpen = true;
    },

    async submitAddRoom() {
      const fmt = this.addRoomFormat;
      const lang = this.addRoomLanguage;
      this.addRoomOpen = false;
      await this.send("POST", "/rooms", { format: fmt, language: lang });
    },

    adminUrl() {
      return location.href;
    },

    participantUrl() {
      if (!this.event) return "";
      return `${location.origin}/register?event=${this.event.code}`;
    },

    async copyAdminUrl() {
      if (await copyText(this.adminUrl())) {
        this.copyState = "Copied ✓";
        setTimeout(() => { this.copyState = "Copy admin link"; }, 1500);
      }
    },

    async copyParticipantUrl() {
      const url = this.participantUrl();
      if (!url) return;
      if (await copyText(url)) {
        this.copyParticipantState = "Copied ✓";
        setTimeout(() => { this.copyParticipantState = "Copy"; }, 1500);
      }
    },

    dismissFirstVisit() {
      localStorage.setItem(`admin_seen_${this.adminToken}`, "1");
      this.firstVisit = false;
    },

    onEscape() {
      if (this.renaming) this.cancelRename();
      else if (this.recoverOpen) this.closeRecover();
      else if (this.addRoomOpen) this.addRoomOpen = false;
      else if (this.firstVisit) this.dismissFirstVisit();
      else if (this.selectedPid !== null) this.clearSelection();
    },

    async deleteParticipant(p) {
      if (!confirm(
        `Delete "${p.name}" from this event? They'll be removed from any `
        + `slot they're in. This can't be undone.`
      )) return;
      await this.send("DELETE", `/participants/${p.id}`);
    },

    async deleteRoom(roomId) {
      if (!confirm("Delete this room and unassign its slots?")) return;
      await this.send("DELETE", `/rooms/${roomId}`);
    },

    async assignParticipant(slotId, participantId) {
      await this.send("PATCH", `/slots/${slotId}`, { participant_id: participantId });
    },

    async clearSlot(slotId) {
      await this.send("PATCH", `/slots/${slotId}`, { participant_id: null });
    },

    // ───── Click-to-place (drag alternative) ─────────────────────────────
    clearSelection() {
      this.selectedPid = null;
      this.selectedSlotId = null;
    },

    // Highlight helpers used by the templates' :class bindings.
    isPoolSelected(pid) {
      return this.selectedPid === pid && this.selectedSlotId === null;
    },
    isSlotSelected(slotId) {
      return this.selectedSlotId === slotId;
    },

    // Click on a participant in the left-hand list.
    onParticipantClick(p) {
      if (!this.event || this.event.status !== "closed") return;
      // Clicking the same picked-up pool card again clears the focus.
      if (this.isPoolSelected(p.id)) { this.clearSelection(); return; }
      // A slot card is picked up and the user clicked a pool card → drop the
      // pool card into that slot (its previous occupant returns to the pool).
      if (this.selectedSlotId !== null) {
        const destSlot = this.selectedSlotId;
        this.flyInto(`#plist [data-participant-id="${p.id}"]`,
                     `[data-slot-id="${destSlot}"]`);
        this.assignParticipant(destSlot, p.id);
        this.clearSelection();
        return;
      }
      // Otherwise just pick up this pool card.
      this.selectedPid = p.id;
      this.selectedSlotId = null;
    },

    // Click on a slot (filled card or empty placeholder) inside a room.
    onSlotClick(slot) {
      if (!this.event || this.event.status !== "closed") return;
      const targetSlotId = slot.slot_id;
      const targetPid = slot.participant ? slot.participant.id : null;

      // Nothing picked up yet: clicking a filled slot picks it up.
      if (this.selectedPid === null) {
        if (targetPid !== null) {
          this.selectedPid = targetPid;
          this.selectedSlotId = targetSlotId;
        }
        return;
      }

      // Clicking the same slot again clears the focus.
      if (this.selectedSlotId === targetSlotId) { this.clearSelection(); return; }

      const srcPid = this.selectedPid;
      const srcSlotId = this.selectedSlotId;

      if (targetPid !== null && srcSlotId !== null) {
        // Two occupied slots → swap the two cards.
        this.swapSlots(srcSlotId, targetSlotId);
      } else {
        // Target empty, or source from the pool → place source into target.
        // (If the target was occupied and source came from the pool, the
        // backend displaces the occupant back to the pool.)
        const srcSel = srcSlotId !== null
          ? `[data-slot-id="${srcSlotId}"] .slot-card`
          : `#plist [data-participant-id="${srcPid}"]`;
        this.flyInto(srcSel, `[data-slot-id="${targetSlotId}"]`);
        this.assignParticipant(targetSlotId, srcPid);
      }
      this.clearSelection();
    },

    // Swap the occupants of two slots in ONE atomic request. (Two separate
    // PATCHes could leave the overlay stuck if the second response was slow
    // to arrive — the backend now does the exchange in a single transaction.)
    swapSlots(s1, s2) {
      const el1 = document.querySelector(`[data-slot-id="${s1}"]`);
      const el2 = document.querySelector(`[data-slot-id="${s2}"]`);
      if (el1 && el2) {
        this._flyClone(el1.querySelector(".slot-card") || el1, el2, false);
        this._flyClone(el2.querySelector(".slot-card") || el2, el1, false);
      }
      this.send("POST", `/slots/${s1}/swap/${s2}`);
    },

    // Animate a card flying from `srcSel` into `destSel`, fading as it lands.
    flyInto(srcSel, destSel) {
      const src = document.querySelector(srcSel);
      const dest = document.querySelector(destSel);
      if (src && dest) this._flyClone(src, dest, true);
    },

    // Clone an element and transition it to another element's position. The
    // clone is a fixed overlay, so it survives Alpine's authoritative
    // re-render of the slots underneath it.
    _flyClone(srcEl, destEl, fade) {
      const s = srcEl.getBoundingClientRect();
      const d = destEl.getBoundingClientRect();
      const clone = srcEl.cloneNode(true);
      // The clone still carries Alpine directives (x-text, :class, x-show).
      // It lives at <body> level, outside any x-data scope, so Alpine's global
      // observer would try to evaluate them and spam "x is not defined". Strip
      // every Alpine attribute and flag the subtree as ignored.
      this._stripAlpine(clone);
      clone.setAttribute("x-ignore", "");
      clone.classList.add("fly-clone");
      Object.assign(clone.style, {
        position: "fixed",
        left: `${s.left}px`, top: `${s.top}px`,
        width: `${s.width}px`, height: `${s.height}px`,
        margin: "0", zIndex: "9999", pointerEvents: "none",
        transition: "transform .28s cubic-bezier(.2,.8,.2,1), opacity .28s ease",
      });
      document.body.appendChild(clone);
      const dx = (d.left + d.width / 2) - (s.left + s.width / 2);
      const dy = (d.top + d.height / 2) - (s.top + s.height / 2);
      requestAnimationFrame(() => {
        clone.style.transform = `translate(${dx}px, ${dy}px) scale(.96)`;
        if (fade) clone.style.opacity = ".35";
      });
      setTimeout(() => clone.remove(), 320);
    },

    // Remove Alpine directives (x-*, :bind, @on) from a detached node tree so
    // it renders as a static snapshot and Alpine never tries to evaluate it.
    _stripAlpine(root) {
      const nodes = [root, ...root.querySelectorAll("*")];
      for (const el of nodes) {
        for (const attr of [...el.attributes]) {
          const n = attr.name;
          if (n.startsWith("x-") || n.startsWith(":") || n.startsWith("@")) {
            el.removeAttribute(n);
          }
        }
      }
    },

    async toggleLock(slot) {
      await this.send("PATCH", `/slots/${slot.slot_id}`, { locked: !slot.locked });
    },

    async addJudgeSlot(room) {
      await this.send("POST", `/rooms/${room.room_id}/judge-slots`);
    },

    async addFreeSlot(room) {
      await this.send("POST", `/rooms/${room.room_id}/free-slots`);
    },

    async seedDemo() {
      await this.post("seed-demo");
    },

    // ───── Recover from logs ─────────────────────────────────────────────
    openRecover() {
      this.recoverText = "";
      this.recoverPreviewCount = 0;
      this.recoverPreviewNames = "";
      this.recoverOtherCodes = [];
      this.recoverResult = null;
      this.recoverBusy = false;
      this.recoverOpen = true;
    },

    closeRecover() {
      if (this.recoverBusy) return;
      clearTimeout(this._recoverPreviewTimer);
      this.recoverOpen = false;
    },

    // Debounced live preview: ask the server (single source of truth for the
    // parser) what it would recover, without touching the DB.
    onRecoverInput() {
      this.recoverResult = null;
      clearTimeout(this._recoverPreviewTimer);
      if (this.recoverText.trim().length === 0) {
        this.recoverPreviewCount = 0;
        this.recoverPreviewNames = "";
        this.recoverOtherCodes = [];
        return;
      }
      this._recoverPreviewTimer = setTimeout(() => this._fetchRecoverPreview(), 250);
    },

    async _fetchRecoverPreview() {
      const text = this.recoverText;
      try {
        const res = await this._recoverRequest(text, true);
        // Ignore a stale response if the textarea changed while in flight.
        if (text !== this.recoverText) return;
        this.recoverPreviewCount = res.detected;
        this.recoverOtherCodes = res.other_event_codes || [];
        const names = res.names || [];
        const shown = names.slice(0, 12).join(", ");
        this.recoverPreviewNames =
          names.length > 12 ? `${shown}, +${names.length - 12} more` : shown;
      } catch (_) {
        // Preview is best-effort; leave the last good value in place.
      }
    },

    async submitRecover() {
      if (this.recoverPreviewCount === 0) return;
      this.recoverBusy = true;
      this.recoverResult = null;
      try {
        const res = await this._recoverRequest(this.recoverText, false);
        await this.refresh();
        let msg = `Recovered ${res.recovered} participant`
          + `${res.recovered === 1 ? "" : "s"}.`;
        if (res.skipped > 0) msg += ` ${res.skipped} skipped (duplicate names).`;
        this.recoverResult = msg;
        // Leave the result visible briefly, then close.
        setTimeout(() => { this.recoverOpen = false; }, 1800);
      } catch (e) {
        this.recoverResult = null;
        this.errorMsg = e.message;
      } finally {
        this.recoverBusy = false;
      }
    },

    async _recoverRequest(log, dryRun) {
      const r = await fetch(this.api("/recover-from-log"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ log, dry_run: dryRun }),
      });
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { const j = await r.json(); detail = j.detail || detail; } catch (_) {}
        throw new Error(detail);
      }
      return r.json();
    },

    async confirmClearUnlocked() {
      const filledUnlocked = this.rooms.reduce(
        (acc, r) => acc + r.slots.filter(
          (s) => s.participant && !s.locked
        ).length, 0
      );
      if (filledUnlocked === 0) {
        this.errorMsg = "Nothing to clear — no unlocked slots are filled.";
        return;
      }
      if (!confirm(
        `Vacate ${filledUnlocked} unlocked slot(s)? Locked slots stay.`
      )) return;
      await this.post("clear-unlocked");
    },

    // Close + immediately auto-propose so the tabmaster sees a proposal
    // the moment registration closes.
    async closeRegistration() {
      await this.post("close-registration");
      if (this.event && this.event.status === "closed"
          && this.rooms.length === 0
          && this.participants.length > 0) {
        await this.post("propose-rooms");
      }
    },

    // ───── Room renaming ─────────────────────────────────────────────────
    startRename(room) {
      this.renaming = room.room_id;
      this.renamingName = room.name || "";
      this.$nextTick(() => {
        const el = document.getElementById(`rn-${room.room_id}`);
        if (el) { el.focus(); el.select(); }
      });
    },

    async saveRename(room) {
      const id = room.room_id;
      const name = this.renamingName;
      this.renaming = null;
      await this.send("PATCH", `/rooms/${id}`, { name });
    },

    cancelRename() {
      this.renaming = null;
      this.renamingName = "";
    },

    // ───── Auto-proposal card ───────────────────────────────────────────
    showProposalCard() {
      return this.event && this.event.status === "closed"
        && this.rooms.length > 0
        && !this.proposalDismissed;
    },

    proposalBreakdown() {
      const counts = {};
      for (const r of this.rooms) {
        const key = `${r.format} · ${this.langFlag(r.language)}`;
        counts[key] = (counts[key] || 0) + 1;
      }
      return Object.entries(counts).map(([k, n]) => `${n}× ${k}`);
    },

    dismissProposal() {
      localStorage.setItem(`proposal_dismissed_${this.adminToken}`, "1");
      this.proposalDismissed = true;
    },

    async clearAllRooms() {
      if (!confirm("Discard all proposed rooms?")) return;
      for (const r of [...this.rooms]) {
        await this.send("DELETE", `/rooms/${r.room_id}`);
      }
      this.dismissProposal();
    },

    async confirmPublish() {
      const unfilled = this.rooms.reduce(
        (acc, r) => acc + r.slots.filter((s) => !s.participant).length, 0
      );
      const msg = unfilled > 0
        ? `Publish? ${unfilled} slot(s) are still empty.`
        : "Publish? Participants will see their assignments.";
      if (!confirm(msg)) return;
      await this.post("publish");
    },
  };
}
