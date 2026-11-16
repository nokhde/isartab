"use strict";

const bpSpeakers = [
  "Prime Minister",
  "Leader of Opposition",
  "Deputy Prime Minister",
  "Deputy Leader of Opposition",
  "Member of Government",
  "Member of Opposition",
  "Government Whip",
  "Opposition Whip",
];
const opdOpeningSpeakers = [
  "First Speaker of Government",
  "First Speaker of Opposition",
  "Second Speaker of Government",
  "Second Speaker of Opposition",
];
const phases = ["Protected time", "Unprotected time", "Protected time", "Grace period", "Time is up"];
const poiDuration = 15_000;
const preferenceKey = "isartab.timeturner";
const defaults = { speech: 420, grace: 15, freeSpeakers: 3, volume: 70, format: "bp" };

const byId = (id) => document.getElementById(id);
const timerPanel = byId("timer-panel");
const countdown = byId("countdown");
const phase = byId("phase");
const segments = document.querySelectorAll(".timeline span");
const toggle = byId("toggle");
const poi = byId("poi");
const previous = byId("previous");
const next = byId("next");
const reset = byId("reset");
const soundDialog = byId("sound");
const settingsDialog = byId("settings");
const speechInput = byId("speech-duration");
const graceInput = byId("grace-duration");
const freeSpeakersInput = byId("free-speakers");
const volumeInput = byId("volume");
const testBell = byId("test-bell");

function wholeNumber(value, min, max = Number.MAX_SAFE_INTEGER) {
  return Number.isSafeInteger(value) && value >= min && value <= max;
}

function loadPreferences() {
  try {
    const stored = JSON.parse(localStorage.getItem(preferenceKey));
    if (stored) {
      return {
        speech: wholeNumber(stored.speech, 121, 59_999) ? stored.speech : defaults.speech,
        grace: wholeNumber(stored.grace, 1, 599) ? stored.grace : defaults.grace,
        freeSpeakers: wholeNumber(stored.freeSpeakers, 0, Number.MAX_SAFE_INTEGER - 6)
          ? stored.freeSpeakers : defaults.freeSpeakers,
        volume: wholeNumber(stored.volume, 0, 100) ? stored.volume : defaults.volume,
        format: stored.format === "opd" ? "opd" : "bp",
      };
    }
  } catch {
    // Storage may be unavailable in private browsing or restricted contexts.
  }
  return { ...defaults };
}

const preferences = loadPreferences();
let speakerIndex = 0;
let boundaries;
let elapsed = 0;
let startedAt = null;
let nextCue = 0;
let poiEndsAt = null;
let deferredCues = [];
let tickId = null;
let audio;
let bellVolume;
let bellAvailable = false;
let nextStrike = 0;
const bellNodes = new Set();

function savePreferences() {
  try {
    localStorage.setItem(preferenceKey, JSON.stringify(preferences));
  } catch {
    // Preferences still apply for the current page.
  }
}

function speakerCount() {
  return preferences.format === "bp" ? bpSpeakers.length : 6 + preferences.freeSpeakers;
}

function currentSpeaker() {
  if (preferences.format === "bp") return bpSpeakers[speakerIndex];
  if (speakerIndex < opdOpeningSpeakers.length) return opdOpeningSpeakers[speakerIndex];
  if (isFreeSpeaker()) return `Free Speaker ${speakerIndex - opdOpeningSpeakers.length + 1}`;

  const oppositionCloses = preferences.freeSpeakers === 0;
  const finalSpeaker = speakerIndex === speakerCount() - 1;
  return finalSpeaker === oppositionCloses ? "Third Speaker of Opposition" : "Third Speaker of Government";
}

function isFreeSpeaker() {
  return preferences.format === "opd" &&
    speakerIndex >= opdOpeningSpeakers.length && speakerIndex < speakerCount() - 2;
}

function clockTime(seconds) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function renderClock(now) {
  const activePhase = nextCue;
  const remaining = clockTime(Math.ceil((boundaries[3] - elapsed) / 1000));
  if (countdown.textContent !== remaining) countdown.textContent = remaining;
  countdown.classList.toggle("long-duration", remaining.length > 5);
  if (phase.dataset.phase !== String(activePhase)) {
    phase.dataset.phase = activePhase;
    phase.textContent = phases[activePhase];
  }

  let segmentStart = 0;
  segments.forEach((segment, index) => {
    const duration = boundaries[index] - segmentStart;
    const fill = Math.max(0, Math.min(1, (elapsed - segmentStart) / duration));
    segment.style.setProperty("--duration", duration);
    segment.style.setProperty("--fill", `${fill * 100}%`);
    segmentStart = boundaries[index];
  });

  const poiActive = poiEndsAt !== null;
  poi.textContent = poiActive ? `End POI · ${Math.ceil((poiEndsAt - now) / 1000)}s` : "POI";
  poi.setAttribute("aria-pressed", String(poiActive));
  poi.setAttribute("aria-label", poiActive ? "End point of information" : "Start point of information");
  poi.disabled = !poiActive && startedAt === null;
  testBell.disabled = poiActive;
  toggle.disabled = activePhase === 4 && poiActive;
  toggle.textContent = startedAt !== null ? "Stop" : activePhase === 4 ? "Start again" : elapsed > 0 ? "Resume" : "Start";
  reset.disabled = startedAt === null && elapsed === 0;
}

function renderSpeaker() {
  byId("speaker-name").textContent = currentSpeaker();
  byId("speaker-index").textContent = `Speaker ${speakerIndex + 1} of ${speakerCount()}`;
  previous.disabled = speakerIndex === 0;
  next.disabled = speakerIndex === speakerCount() - 1;
  for (const format of ["bp", "opd"]) {
    byId(format).setAttribute("aria-pressed", String(format === preferences.format));
  }
}

function resetClock() {
  clearTimeout(tickId);
  cancelBells();
  startedAt = null;
  elapsed = 0;
  nextCue = 0;
  poiEndsAt = null;
  deferredCues = [];
  const speech = isFreeSpeaker() ? 210 : preferences.speech;
  const closingProtection = isFreeSpeaker() ? 30 : 60;
  boundaries = [60, speech - closingProtection, speech, speech + preferences.grace]
    .map((seconds) => seconds * 1000);
  byId("poi-status").textContent = "";
  renderClock(performance.now());
}

function changeSpeaker(direction) {
  const target = speakerIndex + direction;
  if (target < 0 || target >= speakerCount()) return;
  speakerIndex = target;
  resetClock();
  renderSpeaker();
}

async function enableAudio() {
  try {
    if (!audio) {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      audio = new AudioContext();
      bellVolume = audio.createGain();
      bellVolume.gain.value = preferences.volume / 100;
      bellVolume.connect(audio.destination);
    }
    if (audio.state !== "running") await audio.resume();
    bellAvailable = true;
  } catch {
    bellAvailable = false;
  }

  for (const id of ["audio-status", "sound-error"]) {
    const status = byId(id);
    status.hidden = bellAvailable;
    status.textContent = bellAvailable ? "" : "Bell audio is unavailable in this browser.";
  }
  return bellAvailable;
}

function cancelBells() {
  for (const node of bellNodes) node.stop();
  bellNodes.clear();
  nextStrike = 0;
}

function ring(count) {
  if (!bellAvailable) return;

  // Inharmonic partials with short attacks and separate decays approximate a struck bell.
  const tones = [
    { frequency: 740, amplitude: 0.34, decay: 0.55 },
    { frequency: 1490, amplitude: 0.18, decay: 0.34 },
    { frequency: 2050, amplitude: 0.12, decay: 0.19 },
    { frequency: 3230, amplitude: 0.06, decay: 0.11 },
  ];
  for (let strike = 0; strike < count; strike += 1) {
    const start = Math.max(audio.currentTime + 0.01, nextStrike);
    for (const { frequency, amplitude, decay } of tones) {
      const oscillator = audio.createOscillator();
      const envelope = audio.createGain();
      oscillator.frequency.value = frequency;
      envelope.gain.setValueAtTime(0.001, start);
      envelope.gain.exponentialRampToValueAtTime(amplitude, start + 0.008);
      envelope.gain.exponentialRampToValueAtTime(0.001, start + decay);
      oscillator.connect(envelope);
      envelope.connect(bellVolume);
      oscillator.onended = () => {
        bellNodes.delete(oscillator);
        envelope.disconnect();
      };
      bellNodes.add(oscillator);
      oscillator.start(start);
      oscillator.stop(start + decay);
    }
    nextStrike = start + 0.36;
  }
  nextStrike += 0.22;
}

function endPoi(expired) {
  poiEndsAt = null;
  if (expired) ring(1);
  for (const count of deferredCues) ring(count);
  deferredCues = [];
  byId("poi-status").textContent = expired ? "Point of information time is up." : "Point of information ended.";
}

function updateClock(now) {
  if (startedAt !== null) {
    const cueCounts = [1, 1, 2, 3];
    elapsed = Math.min(now - startedAt, boundaries[3]);
    while (elapsed >= boundaries[nextCue]) {
      const cueAt = startedAt + boundaries[nextCue];
      if (poiEndsAt !== null && cueAt >= poiEndsAt) endPoi(true);
      const count = cueCounts[nextCue];
      if (poiEndsAt === null) ring(count);
      else deferredCues.push(count);
      nextCue += 1;
    }
    if (nextCue === boundaries.length) startedAt = null;
  }

  if (poiEndsAt !== null && now >= poiEndsAt) endPoi(true);
  renderClock(now);
}

function tick() {
  clearTimeout(tickId);
  updateClock(performance.now());
  if (startedAt !== null || poiEndsAt !== null) tickId = setTimeout(tick, 50);
}

toggle.addEventListener("click", () => {
  const now = performance.now();
  const wasRunning = startedAt !== null;
  updateClock(now);
  if (wasRunning) {
    startedAt = null;
    if (poiEndsAt !== null) endPoi(false);
  } else {
    if (nextCue === boundaries.length) resetClock();
    void enableAudio();
    startedAt = now - elapsed;
  }
  tick();
});

poi.addEventListener("click", () => {
  const now = performance.now();
  const wasActive = poiEndsAt !== null;
  updateClock(now);
  if (wasActive) {
    if (poiEndsAt !== null) endPoi(false);
  } else if (startedAt !== null) {
    cancelBells();
    poiEndsAt = now + poiDuration;
    byId("poi-status").textContent = "15-second point of information started.";
  }
  tick();
});

reset.addEventListener("click", resetClock);
previous.addEventListener("click", () => changeSpeaker(-1));
next.addEventListener("click", () => changeSpeaker(1));
for (const format of ["bp", "opd"]) {
  byId(format).addEventListener("click", () => {
    if (format === preferences.format) return;
    preferences.format = format;
    savePreferences();
    speakerIndex = 0;
    resetClock();
    renderSpeaker();
  });
}

let swipeStart = null;
timerPanel.addEventListener("pointerdown", (event) => {
  if (!event.isPrimary || event.pointerType === "mouse" || event.target.closest("button")) return;
  swipeStart = { x: event.clientX, y: event.clientY, id: event.pointerId };
  timerPanel.setPointerCapture(event.pointerId);
});
timerPanel.addEventListener("pointercancel", () => { swipeStart = null; });
timerPanel.addEventListener("pointerup", (event) => {
  if (swipeStart === null || swipeStart.id !== event.pointerId) return;
  const dx = event.clientX - swipeStart.x;
  const dy = event.clientY - swipeStart.y;
  swipeStart = null;
  if (Math.abs(dx) >= 55 && Math.abs(dx) >= 1.5 * Math.abs(dy)) {
    changeSpeaker(dx < 0 ? 1 : -1);
  }
});

function renderVolume() {
  volumeInput.value = preferences.volume;
  byId("volume-value").value = `${preferences.volume}%`;
}

byId("open-sound").addEventListener("click", () => soundDialog.showModal());
volumeInput.addEventListener("input", () => {
  preferences.volume = Number(volumeInput.value);
  if (bellVolume) bellVolume.gain.value = preferences.volume / 100;
  renderVolume();
  savePreferences();
});
testBell.addEventListener("click", async () => {
  if (await enableAudio() && poiEndsAt === null) ring(1);
});

byId("open-settings").addEventListener("click", () => {
  speechInput.value = clockTime(preferences.speech);
  speechInput.setCustomValidity("");
  graceInput.value = preferences.grace;
  freeSpeakersInput.value = preferences.freeSpeakers;
  freeSpeakersInput.setCustomValidity("");
  byId("opd-settings").hidden = preferences.format !== "opd";
  settingsDialog.showModal();
});
byId("cancel-settings").addEventListener("click", () => settingsDialog.close());
speechInput.addEventListener("input", () => speechInput.setCustomValidity(""));
freeSpeakersInput.addEventListener("input", () => freeSpeakersInput.setCustomValidity(""));
byId("settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const [minutes, seconds] = speechInput.value.split(":").map(Number);
  const speech = minutes * 60 + seconds;
  const freeSpeakers = freeSpeakersInput.valueAsNumber;
  if (speech < 121) {
    speechInput.setCustomValidity("Enter at least 2:01 to leave room for unprotected time.");
    speechInput.reportValidity();
    return;
  }
  if (!wholeNumber(freeSpeakers, 0, Number.MAX_SAFE_INTEGER - 6)) {
    freeSpeakersInput.setCustomValidity("Enter a whole number of free speakers.");
    freeSpeakersInput.reportValidity();
    return;
  }
  preferences.speech = speech;
  preferences.grace = graceInput.valueAsNumber;
  preferences.freeSpeakers = freeSpeakers;
  savePreferences();
  speakerIndex = Math.min(speakerIndex, speakerCount() - 1);
  resetClock();
  renderSpeaker();
  settingsDialog.close();
});

document.addEventListener("visibilitychange", tick);
renderVolume();
resetClock();
renderSpeaker();
