#!/usr/bin/env bash
# Chunk 6 end-to-end smoke test.
#
# Proposal §8 Chunk 6 'done when':
#   create event → register ~25 → close → propose → assign+lock two via
#   PATCH /slots → magic-fill → publish → verify /me reflects slot and
#   /rooms?…&me=… highlights the right slot.

set -euo pipefail

cd "$(dirname "$0")/.."

PORT=8765
BASE="http://127.0.0.1:${PORT}"
CONTAINER="debate-smoke-admin"
N=25

cleanup() { docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

cleanup
docker build -t debate-allocator . >/dev/null
docker run --rm -d --name "${CONTAINER}" -p "${PORT}:8000" debate-allocator >/dev/null

echo "→ waiting for server"
until curl -fsS "${BASE}/healthz" >/dev/null 2>&1; do sleep 0.3; done

# Helpers
jget() { python3 -c "import json,sys; print($1)" <<<"$2"; }

echo "→ create event"
ADMIN_URL=$(curl -fsS -o /dev/null -w '%{redirect_url}' -X POST "${BASE}/events")
ADMIN_TOKEN="${ADMIN_URL##*/}"   # last path segment (works for /created/{tok} and /admin/{tok})
STATE=$(curl -fsS "${BASE}/api/admin/${ADMIN_TOKEN}/state")
CODE=$(jget 'json.load(sys.stdin)["event"]["code"]' "${STATE}")
echo "  code=${CODE} admin_token=${ADMIN_TOKEN:0:8}…"

echo "→ register ${N} participants"
LANGS=("EN" "DE" "DE/EN")
FMTS=("BP" "BP" "BP" "BP" "OPD")
ROLES=("S" "S" "S" "S" "J" "J" "SJ")
EXPS=(1 2 3)
for i in $(seq 1 "${N}"); do
  NAME=$(printf 'P%02d' "${i}")
  BTOK=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
  if [ "${i}" -eq 1 ]; then FIRST_BTOK="${BTOK}"; FIRST_NAME="${NAME}"; fi
  if [ "${i}" -eq 2 ]; then SECOND_BTOK="${BTOK}"; SECOND_NAME="${NAME}"; fi
  L=${LANGS[$((RANDOM % ${#LANGS[@]}))]}
  F=${FMTS[$((RANDOM % ${#FMTS[@]}))]}
  R=${ROLES[$((RANDOM % ${#ROLES[@]}))]}
  E=${EXPS[$((RANDOM % ${#EXPS[@]}))]}
  BODY=$(printf '{"browser_token":"%s","name":"%s","language":"%s","format":"%s","role":"%s","could_speak_last":true,"experience":%d}' \
    "${BTOK}" "${NAME}" "${L}" "${F}" "${R}" "${E}")
  curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "${BODY}" "${BASE}/api/events/${CODE}/participants" >/dev/null
done

PUB=$(curl -fsS "${BASE}/api/events/${CODE}/public")
COUNT=$(jget 'json.load(sys.stdin)["participant_count"]' "${PUB}")
[ "${COUNT}" = "${N}" ] || { echo "expected count=${N}, got ${COUNT}"; exit 1; }
echo "  participant_count=${COUNT}"

echo "→ GET /admin/{token} (HTML)"
HTML=$(curl -fsS "${BASE}/admin/${ADMIN_TOKEN}")
printf '%s' "${HTML}" | grep -q 'adminPanel' || { echo "admin.html missing Alpine bootstrap"; exit 1; }
printf '%s' "${HTML}" | grep -q "${ADMIN_TOKEN}" || { echo "admin.html missing admin_token"; exit 1; }
echo "  ok"

echo "→ GET /admin/badtoken → 404"
RC=$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/admin/0000000000000000000000000000bad0")
[ "${RC}" = "404" ] || { echo "expected 404, got ${RC}"; exit 1; }

echo "→ propose-rooms while open should 409"
RC=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  "${BASE}/api/admin/${ADMIN_TOKEN}/propose-rooms")
[ "${RC}" = "409" ] || { echo "expected 409, got ${RC}"; exit 1; }

echo "→ close registration"
curl -fsS -X POST "${BASE}/api/admin/${ADMIN_TOKEN}/close-registration" >/dev/null

echo "→ propose-rooms (solver runs)"
RESP=$(curl -fsS -X POST "${BASE}/api/admin/${ADMIN_TOKEN}/propose-rooms")
NUM_ROOMS=$(jget 'len(json.load(sys.stdin)["rooms"])' "${RESP}")
[ "${NUM_ROOMS}" -ge 1 ] || { echo "expected >=1 room, got ${NUM_ROOMS}"; exit 1; }
echo "  proposed ${NUM_ROOMS} rooms"

# Grab the first room's first slot id, and an OG-ish slot from room 0.
FIRST_SLOT_ID=$(jget 'json.load(sys.stdin)["rooms"][0]["slots"][0]["slot_id"]' "${RESP}")
SECOND_SLOT_ID=$(jget 'json.load(sys.stdin)["rooms"][0]["slots"][1]["slot_id"]' "${RESP}")

# We need participant ids for P01 and P02.
P1_ID=$(curl -fsS "${BASE}/api/admin/${ADMIN_TOKEN}/state" \
  | python3 -c "import json,sys; print(next(p['id'] for p in json.load(sys.stdin)['participants'] if p['name']=='${FIRST_NAME}'))")
P2_ID=$(curl -fsS "${BASE}/api/admin/${ADMIN_TOKEN}/state" \
  | python3 -c "import json,sys; print(next(p['id'] for p in json.load(sys.stdin)['participants'] if p['name']=='${SECOND_NAME}'))")

echo "→ assign P01 to slot ${FIRST_SLOT_ID}, lock it"
curl -fsS -X PATCH -H 'Content-Type: application/json' \
  -d "{\"participant_id\": ${P1_ID}}" \
  "${BASE}/api/admin/${ADMIN_TOKEN}/slots/${FIRST_SLOT_ID}" >/dev/null
LOCK_RESP=$(curl -fsS -X PATCH -H 'Content-Type: application/json' \
  -d '{"locked": true}' \
  "${BASE}/api/admin/${ADMIN_TOKEN}/slots/${FIRST_SLOT_ID}")
LOCKED=$(printf '%s' "${LOCK_RESP}" | python3 -c "
import json,sys
state=json.load(sys.stdin)
for r in state['rooms']:
    for s in r['slots']:
        if s['slot_id']==${FIRST_SLOT_ID}:
            print('locked' if s['locked'] else 'unlocked', 'pid=' + str(s['participant']['id'] if s['participant'] else None))
")
echo "  ${LOCKED}"
[[ "${LOCKED}" == *locked* ]] || { echo "lock didn't stick"; exit 1; }

echo "→ assign P02 to slot ${SECOND_SLOT_ID} (explicitly unlocked — assign auto-locks)"
curl -fsS -X PATCH -H 'Content-Type: application/json' \
  -d "{\"participant_id\": ${P2_ID}, \"locked\": false}" \
  "${BASE}/api/admin/${ADMIN_TOKEN}/slots/${SECOND_SLOT_ID}" >/dev/null

echo "→ magic-fill"
FILL_RESP=$(curl -fsS -X POST "${BASE}/api/admin/${ADMIN_TOKEN}/magic-fill")
# Locked slot must still hold P01.
HELD=$(printf '%s' "${FILL_RESP}" | python3 -c "
import json,sys
state=json.load(sys.stdin)
for r in state['rooms']:
    for s in r['slots']:
        if s['slot_id']==${FIRST_SLOT_ID}:
            assert s['locked'], 'lock lost'
            assert s['participant'] and s['participant']['id']==${P1_ID}, 'lock-slot participant changed'
print('ok')
")
echo "  locked slot held P01: ${HELD}"

echo "→ publish before any room is empty? publish must 409 if no rooms exist; we have rooms so it should succeed"
PUB_RESP=$(curl -fsS -X POST "${BASE}/api/admin/${ADMIN_TOKEN}/publish")
STATUS=$(jget 'json.load(sys.stdin)["event"]["status"]' "${PUB_RESP}")
[ "${STATUS}" = "published" ] || { echo "expected status=published, got ${STATUS}"; exit 1; }
echo "  status=${STATUS}"

echo "→ GET /api/events/${CODE}/participants/me?token=${FIRST_BTOK:0:8}…"
ME=$(curl -fsS "${BASE}/api/events/${CODE}/participants/me?token=${FIRST_BTOK}")
ME_HAS_SLOT=$(jget 'json.load(sys.stdin)["slot"] is not None' "${ME}")
[ "${ME_HAS_SLOT}" = "True" ] || { echo "expected /me.slot, got null"; exit 1; }
ME_SLOT_ID=$(jget 'json.load(sys.stdin)["slot"]["slot_id"]' "${ME}")
[ "${ME_SLOT_ID}" = "${FIRST_SLOT_ID}" ] || { echo "/me slot mismatch (${ME_SLOT_ID} vs ${FIRST_SLOT_ID})"; exit 1; }
echo "  ok (slot ${ME_SLOT_ID})"

echo "→ GET /rooms?event=${CODE}&me=${FIRST_BTOK:0:8}… highlights P01's slot"
ROOMS_HTML=$(curl -fsS "${BASE}/rooms?event=${CODE}&me=${FIRST_BTOK}")
printf '%s' "${ROOMS_HTML}" | grep -q "Room assignments" || { echo "/rooms missing assignments header"; exit 1; }
printf '%s' "${ROOMS_HTML}" | grep -q "${FIRST_NAME}" || { echo "/rooms missing ${FIRST_NAME}"; exit 1; }
printf '%s' "${ROOMS_HTML}" | grep -q "slot--mine" || { echo "/rooms didn't highlight my slot"; exit 1; }
echo "  ok"

echo "→ post-publish: room-mutation endpoints must 409"
RC=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE}/api/admin/${ADMIN_TOKEN}/magic-fill")
[ "${RC}" = "409" ] || { echo "expected 409, got ${RC}"; exit 1; }
RC=$(curl -s -o /dev/null -w '%{http_code}' -X PATCH \
  -H 'Content-Type: application/json' -d '{"locked": false}' \
  "${BASE}/api/admin/${ADMIN_TOKEN}/slots/${FIRST_SLOT_ID}")
[ "${RC}" = "409" ] || { echo "expected 409, got ${RC}"; exit 1; }
echo "  ok (state machine respected)"

echo
echo "ALL CHECKS PASSED"
