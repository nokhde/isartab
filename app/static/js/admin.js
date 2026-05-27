// Admin panel: Alpine state + SortableJS drag-drop + fetch.
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
    _sortables: [],
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

    // Inline-rename state (only one room can be renamed at a time).
    renaming: null,
    renamingName: "",

    // Auto-proposal explanation card. Per-event dismissal flag.
    proposalDismissed: !!localStorage.getItem(`proposal_dismissed_${adminToken}`),

    async boot() {
      await this.refresh();
      // Re-tick the countdown once per second so it updates live.
      this._interval = setInterval(() => { this.countdownTick++; }, 1000);
      this.$nextTick(() => this.setupDrag());
      this.$watch("rooms", () => this.$nextTick(() => this.setupDrag()));
      this.$watch("participants", () => this.$nextTick(() => this.setupDrag()));
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
      this.participants = state.participants;
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
      else if (this.addRoomOpen) this.addRoomOpen = false;
      else if (this.firstVisit) this.dismissFirstVisit();
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

    async toggleLock(slot) {
      await this.send("PATCH", `/slots/${slot.slot_id}`, { locked: !slot.locked });
    },

    async addJudgeSlot(room) {
      await this.send("POST", `/rooms/${room.room_id}/judge-slots`);
    },

    async seedDemo() {
      await this.post("seed-demo");
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

    // ───── Drag & drop ───────────────────────────────────────────────────
    setupDrag() {
      // Tear down old instances.
      for (const s of this._sortables) s.destroy();
      this._sortables = [];

      if (!this.event || this.event.status !== "closed") return;
      const Sortable = window.Sortable;
      if (!Sortable) return;

      const plist = document.getElementById("plist");
      if (plist) {
        this._sortables.push(new Sortable(plist, {
          group: { name: "participants", pull: "clone", put: false },
          sort: false,
          animation: 120,
          // Clicking the participant's × button must not initiate a drag.
          filter: "button",
          preventOnFilter: false,
        }));
      }

      for (const slotEl of document.querySelectorAll(".slot")) {
        this._sortables.push(new Sortable(slotEl, {
          group: { name: "participants", pull: true, put: true },
          sort: false,
          animation: 120,
          // Only the filled card is draggable. Empty placeholders, the
          // lock toggle and the × button must NOT initiate a drag.
          draggable: ".slot-card:not(.slot-card--empty)",
          filter: "button, .slot-card__controls",
          preventOnFilter: false,
          onAdd: (evt) => {
            const pid = parseInt(evt.item.dataset.participantId, 10);
            const sid = parseInt(slotEl.dataset.slotId, 10);
            // Strip whatever SortableJS dropped in — Alpine will re-render
            // the slot authoritatively from server state.
            evt.item.remove();
            if (Number.isFinite(pid) && Number.isFinite(sid)) {
              this.assignParticipant(sid, pid);
            }
          },
        }));
      }
    },
  };
}
