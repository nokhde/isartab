#!/usr/bin/env bash
# Chunk 5 smoke test: participant pages + static assets.
#
# - Server-rendered routes all return 200 with HTML / proper headers.
# - /sw.js carries the Service-Worker-Allowed: / header.
# - /register and /waiting are event-agnostic shells (their bodies do NOT
#   contain the event code — that's added by JS at runtime).
# - /rooms shows "not published" before publish, and lists slots after.
#   Publishing requires Chunk 6, so the "after-publish" path is manually
#   forced via SQL through the running container.

set -euo pipefail

cd "$(dirname "$0")/.."

PORT=8765
BASE="http://127.0.0.1:${PORT}"
CONTAINER="debate-smoke-pages"

cleanup() { docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

cleanup
docker build -t debate-allocator . >/dev/null
docker run --rm -d --name "${CONTAINER}" -p "${PORT}:8000" debate-allocator >/dev/null

echo "→ waiting for server"
until curl -fsS "${BASE}/healthz" >/dev/null 2>&1; do sleep 0.5; done

echo "→ create event + register two participants"
ADMIN_URL=$(curl -fsS -o /dev/null -w '%{redirect_url}' -X POST "${BASE}/events")
ADMIN_TOKEN="${ADMIN_URL##*/}"   # last path segment (works for /created/{tok} and /admin/{tok})
STATE=$(curl -fsS "${BASE}/api/admin/${ADMIN_TOKEN}/state")
CODE=$(printf '%s' "${STATE}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["event"]["code"])')
echo "  code=${CODE}"

for name in Anna Bilal; do
  BTOK=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
  BODY=$(printf '{"browser_token":"%s","name":"%s","language":"EN","format":"BP","role":"S","could_speak_last":true,"experience":2}' \
    "${BTOK}" "${name}")
  curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "${BODY}" "${BASE}/api/events/${CODE}/participants" >/dev/null
done

echo "→ GET /register?event=${CODE}"
REG=$(curl -fsS -i "${BASE}/register?event=${CODE}")
printf '%s' "${REG}" | grep -qi '^Cache-Control: max-age=300' || { echo "missing Cache-Control header"; exit 1; }
printf '%s' "${REG}" | grep -q '<form id="form"' || { echo "/register has no form"; exit 1; }
printf '%s' "${REG}" | grep -q 'register.js' || { echo "/register missing register.js"; exit 1; }
# Event-agnostic: code must NOT appear inline in the HTML body.
printf '%s' "${REG}" | grep -q "${CODE}" && { echo "register.html leaked event code into shell!"; exit 1; }
echo "  ok (event-agnostic, has form, cache header set)"

echo "→ GET /waiting?event=${CODE}"
WAIT=$(curl -fsS -i "${BASE}/waiting?event=${CODE}")
printf '%s' "${WAIT}" | grep -q 'waiting.js' || { echo "/waiting missing waiting.js"; exit 1; }
printf '%s' "${WAIT}" | grep -q 'id="count"' || { echo "/waiting missing count element"; exit 1; }
printf '%s' "${WAIT}" | grep -q "${CODE}" && { echo "waiting.html leaked event code!"; exit 1; }
echo "  ok"

echo "→ GET /rooms?event=${CODE} (status=open → 'not yet published')"
ROOMS_PRE=$(curl -fsS "${BASE}/rooms?event=${CODE}")
printf '%s' "${ROOMS_PRE}" | grep -q "Rooms not yet published" || { echo "/rooms doesn't show pre-publish message"; exit 1; }
echo "  ok"

echo "→ GET /rooms?event=BADCODE → 404"
RC=$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/rooms?event=999999999")
[ "${RC}" = "404" ] || { echo "expected 404, got ${RC}"; exit 1; }
echo "  ok"

echo "→ static assets"
for p in /static/css/participant.css /static/js/register.js /static/js/waiting.js; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "${BASE}${p}")
  [ "${code}" = "200" ] || { echo "${p} → ${code} (expected 200)"; exit 1; }
done
echo "  ok"

echo "→ /sw.js header check"
SW=$(curl -fsS -i "${BASE}/sw.js")
printf '%s' "${SW}" | grep -qi '^Service-Worker-Allowed: /' || { echo "missing Service-Worker-Allowed header"; exit 1; }
printf '%s' "${SW}" | grep -q "CACHE_NAME" || { echo "/sw.js body doesn't look like the SW source"; exit 1; }
echo "  ok"

echo "→ force publish via SQL (preview of Chunk 6 flow), then re-fetch /rooms"
docker exec "${CONTAINER}" python -c "
import sqlite3
conn = sqlite3.connect('/data/tournaments.db')
conn.execute('UPDATE events SET status=? WHERE code=?', ('published', '${CODE}'))
# Insert a minimal fake room with one filled slot so the template has content.
pid = conn.execute('SELECT id FROM participants WHERE event_code=? ORDER BY id LIMIT 1', ('${CODE}',)).fetchone()[0]
cur = conn.execute('INSERT INTO rooms(event_code, room_index, format, language) VALUES (?,?,?,?)',
                   ('${CODE}', 0, 'BP', 'EN'))
rid = cur.lastrowid
conn.execute('INSERT INTO slots(room_id, role, subrole, slot_index, participant_id, locked) VALUES (?,?,?,?,?,?)',
             (rid, 'speaker', 'OG', 0, pid, 0))
conn.execute('INSERT INTO slots(room_id, role, subrole, slot_index, participant_id, locked) VALUES (?,?,?,?,?,?)',
             (rid, 'judge', 'Panelist', 0, None, 0))
conn.commit()
"

# Grab one participant's browser_token to test the 'me' highlight.
MY_BTOK=$(docker exec "${CONTAINER}" python -c "
import sqlite3
conn = sqlite3.connect('/data/tournaments.db')
print(conn.execute('SELECT browser_token FROM participants WHERE event_code=? ORDER BY id LIMIT 1', ('${CODE}',)).fetchone()[0])
")

ROOMS_PUB=$(curl -fsS "${BASE}/rooms?event=${CODE}&me=${MY_BTOK}")
printf '%s' "${ROOMS_PUB}" | grep -q "Room 1 · BP · EN" || { echo "/rooms missing room header"; exit 1; }
printf '%s' "${ROOMS_PUB}" | grep -q "slot--mine" || { echo "/rooms didn't highlight my slot"; exit 1; }
printf '%s' "${ROOMS_PUB}" | grep -q "(you)" || { echo "/rooms didn't mark 'you'"; exit 1; }
printf '%s' "${ROOMS_PUB}" | grep -q '<em class="muted">empty</em>' || { echo "/rooms missing empty placeholder"; exit 1; }
echo "  ok (room rendered, mine highlighted)"

echo
echo "ALL CHECKS PASSED"
