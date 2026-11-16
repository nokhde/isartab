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
const defaults = {
  speech: 420, protection: [60, 60], grace: 15,
  freeSpeech: 210, freeProtection: [60, 30], freeGrace: 15,
  freeSpeakers: 3, volume: 70, format: "bp",
};

const byId = (id) => document.getElementById(id);
const timerPanel = byId("timer-panel");
const formatSlider = byId("format-slider");
const speechClock = byId("speech-clock");
const speaker = byId("speaker");
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
const freeSpeechInput = byId("free-speech-duration");
const freeGraceInput = byId("free-grace-duration");
const freeSpeakersInput = byId("free-speakers");
const protectionInputs = [byId("opening-protection"), byId("closing-protection")];
const freeProtectionInputs = [byId("free-opening-protection"), byId("free-closing-protection")];
const settingsForm = byId("settings-form");
const volumeInput = byId("volume");
const testBell = byId("test-bell");

function wholeNumber(value, min, max = Number.MAX_SAFE_INTEGER) {
  return Number.isSafeInteger(value) && value >= min && value <= max;
}

function validProtection(values, speech) {
  return Array.isArray(values) && values.length === 2 &&
    wholeNumber(values[0], 1) && wholeNumber(values[1], 1) && values[0] + values[1] < speech;
}

function loadPreferences() {
  try {
    const stored = JSON.parse(localStorage.getItem(preferenceKey));
    if (stored) {
      let speech = wholeNumber(stored.speech, 3, 59_999) ? stored.speech : defaults.speech;
      const protection = validProtection(stored.protection, speech) ? stored.protection : defaults.protection;
      if (!validProtection(protection, speech)) speech = defaults.speech;
      let freeSpeech = wholeNumber(stored.freeSpeech, 3, 59_999) ? stored.freeSpeech : defaults.freeSpeech;
      const freeProtection = validProtection(stored.freeProtection, freeSpeech)
        ? stored.freeProtection : defaults.freeProtection;
      if (!validProtection(freeProtection, freeSpeech)) freeSpeech = defaults.freeSpeech;
      const grace = wholeNumber(stored.grace, 1, 599) ? stored.grace : defaults.grace;
      return {
        speech,
        protection,
        grace,
        freeSpeech,
        freeProtection,
        freeGrace: wholeNumber(stored.freeGrace, 1, 599) ? stored.freeGrace : grace,
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
  const elapsedText = clockTime(Math.floor(elapsed / 1000));
  if (speechClock.textContent !== elapsedText) speechClock.textContent = elapsedText;
  speechClock.classList.toggle("long-duration", elapsedText.length > 5);
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
  for (const animation of speaker.getAnimations()) animation.cancel();
  speaker.parentElement.querySelector(".speaker-outgoing")?.remove();
  byId("speaker-name").textContent = currentSpeaker();
  byId("speaker-index").textContent = `Speaker ${speakerIndex + 1} of ${speakerCount()}`;
  previous.disabled = speakerIndex === 0;
  next.disabled = speakerIndex === speakerCount() - 1;
  formatSlider.dataset.format = preferences.format;
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
  const free = isFreeSpeaker();
  const speech = free ? preferences.freeSpeech : preferences.speech;
  const [opening, closing] = free ? preferences.freeProtection : preferences.protection;
  const grace = free ? preferences.freeGrace : preferences.grace;
  boundaries = [opening, speech - closing, speech, speech + grace]
    .map((seconds) => seconds * 1000);
  byId("poi-status").textContent = "";
  renderClock(performance.now());
}

function changeSpeaker(direction) {
  const target = speakerIndex + direction;
  if (target < 0 || target >= speakerCount()) return;
  const animate = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const outgoing = animate ? speaker.cloneNode(true) : null;
  speakerIndex = target;
  resetClock();
  renderSpeaker();

  if (outgoing) {
    outgoing.removeAttribute("id");
    outgoing.removeAttribute("aria-live");
    outgoing.setAttribute("aria-hidden", "true");
    outgoing.querySelectorAll("[id]").forEach((element) => element.removeAttribute("id"));
    outgoing.classList.add("speaker-outgoing");
    speaker.before(outgoing);

    const timing = { duration: 360, easing: "cubic-bezier(0.22, 1, 0.36, 1)" };
    outgoing.animate([
      { transform: "translateX(0)", opacity: 1 },
      { transform: `translateX(${-direction * 75}%)`, opacity: 0 },
    ], timing).onfinish = () => outgoing.remove();
    speaker.animate([
      { transform: `translateX(${direction * 75}%)`, opacity: 0 },
      { transform: "translateX(0)", opacity: 1 },
    ], timing);
  }
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

  // Approximate the hum, prime, tierce, quint, and nominal of a struck bell.
  // The brighter partials decay first, leaving the lower tones to ring.
  const tones = [
    { frequency: 330, amplitude: 0.03, decay: 0.32, tail: 0.40 },
    { frequency: 660, amplitude: 0.83, decay: 0.46, tail: 0.94 },
    { frequency: 792, amplitude: 0.03, decay: 0.26, tail: 0.27 },
    { frequency: 990, amplitude: 0.02, decay: 0.19, tail: 0.18 },
    { frequency: 1320, amplitude: 0.08, decay: 0.10, tail: 0.12 },
  ];
  const duration = 0.55;
  for (let strike = 0; strike < count; strike += 1) {
    const start = Math.max(audio.currentTime + 0.01, nextStrike);
    for (const { frequency, amplitude, decay, tail } of tones) {
      const oscillator = audio.createOscillator();
      const envelope = audio.createGain();
      oscillator.frequency.value = frequency;
      envelope.gain.setValueAtTime(0, start);
      envelope.gain.linearRampToValueAtTime(amplitude, start + 0.004);
      envelope.gain.exponentialRampToValueAtTime(amplitude * tail, start + decay);
      envelope.gain.linearRampToValueAtTime(0, start + duration);
      oscillator.connect(envelope);
      envelope.connect(bellVolume);
      oscillator.onended = () => {
        bellNodes.delete(oscillator);
        envelope.disconnect();
      };
      bellNodes.add(oscillator);
      oscillator.start(start);
      oscillator.stop(start + duration);
    }
    nextStrike = start + duration + 0.02;
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
function changeFormat(format) {
  if (format === preferences.format) return;
  preferences.format = format;
  savePreferences();
  speakerIndex = 0;
  resetClock();
  renderSpeaker();
}

for (const format of ["bp", "opd"]) {
  byId(format).addEventListener("click", () => changeFormat(format));
}

function handleSwipe(element, onSwipe, { fromButtons = false, minDistance = 55 } = {}) {
  let start = null;
  let swiped = false;
  element.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary) return;
    swiped = false;
    if (event.button !== 0) return;
    if (!fromButtons && (event.pointerType === "mouse" || event.target.closest("button"))) return;
    start = { x: event.clientX, y: event.clientY, id: event.pointerId };
    // Keep the original target so tapping a slider button still clicks it.
    event.target.setPointerCapture(event.pointerId);
  });
  element.addEventListener("pointercancel", () => { start = null; });
  element.addEventListener("pointerup", (event) => {
    if (start === null || start.id !== event.pointerId) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    start = null;
    if (Math.abs(dx) < minDistance || Math.abs(dx) < 1.5 * Math.abs(dy)) return;
    swiped = true;
    onSwipe(dx);
  });
  if (fromButtons) {
    element.addEventListener("click", (event) => {
      if (swiped && event.detail > 0) event.stopPropagation();
    }, { capture: true });
  }
}

handleSwipe(timerPanel, (dx) => changeSpeaker(dx < 0 ? 1 : -1));
handleSwipe(formatSlider, (dx) => changeFormat(dx > 0 ? "opd" : "bp"), {
  fromButtons: true,
  minDistance: 24,
});
formatSlider.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  event.preventDefault();
  const format = event.key === "ArrowRight" ? "opd" : "bp";
  changeFormat(format);
  byId(format).focus();
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
  graceInput.value = preferences.grace;
  freeSpeechInput.value = clockTime(preferences.freeSpeech);
  freeGraceInput.value = preferences.freeGrace;
  freeSpeakersInput.value = preferences.freeSpeakers;
  protectionInputs.forEach((input, index) => { input.value = preferences.protection[index]; });
  freeProtectionInputs.forEach((input, index) => { input.value = preferences.freeProtection[index]; });
  clearSettingsErrors();
  byId("opd-settings").hidden = preferences.format !== "opd";
  settingsDialog.showModal();
});
byId("cancel-settings").addEventListener("click", () => settingsDialog.close());
function clearSettingsErrors() {
  for (const input of [freeSpeakersInput, protectionInputs[1], freeProtectionInputs[1]]) {
    input.setCustomValidity("");
  }
}
settingsForm.addEventListener("input", clearSettingsErrors);
function readSpeechDuration(input) {
  const [minutes, seconds] = input.value.split(":").map(Number);
  return minutes * 60 + seconds;
}

settingsForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const speech = readSpeechDuration(speechInput);
  const freeSpeech = readSpeechDuration(freeSpeechInput);
  const freeSpeakers = freeSpeakersInput.valueAsNumber;
  const protection = protectionInputs.map((input) => input.valueAsNumber);
  const freeProtection = freeProtectionInputs.map((input) => input.valueAsNumber);
  for (const [values, duration, closingInput] of [
    [protection, speech, protectionInputs[1]],
    [freeProtection, freeSpeech, freeProtectionInputs[1]],
  ]) {
    if (!validProtection(values, duration)) {
      closingInput.setCustomValidity("Leave at least one second of unprotected time.");
      closingInput.reportValidity();
      return;
    }
  }
  if (!wholeNumber(freeSpeakers, 0, Number.MAX_SAFE_INTEGER - 6)) {
    freeSpeakersInput.setCustomValidity("Enter a whole number of free speakers.");
    freeSpeakersInput.reportValidity();
    return;
  }
  preferences.speech = speech;
  preferences.freeSpeech = freeSpeech;
  preferences.freeGrace = freeGraceInput.valueAsNumber;
  preferences.protection = protection;
  preferences.freeProtection = freeProtection;
  preferences.grace = graceInput.valueAsNumber;
  preferences.freeSpeakers = freeSpeakers;
  savePreferences();
  speakerIndex = Math.min(speakerIndex, speakerCount() - 1);
  resetClock();
  renderSpeaker();
  settingsDialog.close();
});

// Safari exposes pinch gestures separately from pointer events.
for (const type of ["gesturestart", "gesturechange"]) {
  document.addEventListener(type, (event) => event.preventDefault(), { passive: false });
}
document.addEventListener("visibilitychange", tick);
renderVolume();
resetClock();
renderSpeaker();
