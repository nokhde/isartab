#!/usr/bin/env bash
# Chunk 4 smoke test.
#
# Proposal §8 Chunk 4 'done when': with curl you can create an event,
# register 5 participants, close registration, and GET /state returns them.
#
# Assumes:
#   - Docker daemon running.
#   - Image `debate-allocator` already built (or will be built here).
#   - Host port 8765 is free.

set -euo pipefail

cd "$(dirname "$0")/.."

PORT=8765
BASE="http://127.0.0.1:${PORT}"
CONTAINER="debate-smoke-api"

cleanup() { docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

cleanup
docker images --format '{{.Repository}}' | grep -qx debate-allocator \
  || docker build -t debate-allocator . >/dev/null
docker run --rm -d --name "${CONTAINER}" -p "${PORT}:8000" debate-allocator >/dev/null

echo "→ waiting for server"
until curl -fsS "${BASE}/healthz" >/dev/null 2>&1; do sleep 0.5; done

echo "→ POST /events"
ADMIN_URL=$(curl -fsS -o /dev/null -w '%{redirect_url}' -X POST "${BASE}/events")
ADMIN_TOKEN="${ADMIN_URL##*/}"   # last path segment (works for /created/{tok} and /admin/{tok})
[ ${#ADMIN_TOKEN} -eq 32 ] || { echo "bad admin_token: ${ADMIN_TOKEN}"; exit 1; }
echo "  admin_token=${ADMIN_TOKEN:0:8}… (32 chars)"

echo "→ GET /state for event code"
STATE=$(curl -fsS "${BASE}/api/admin/${ADMIN_TOKEN}/state")
CODE=$(printf '%s' "${STATE}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["event"]["code"])')
STATUS=$(printf '%s' "${STATE}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["event"]["status"])')
[ ${#CODE} -eq 9 ] || { echo "bad event code: ${CODE}"; exit 1; }
[ "${STATUS}" = "open" ] || { echo "expected status=open, got ${STATUS}"; exit 1; }
echo "  code=${CODE}  status=${STATUS}"

echo "→ POST /participants (5 times)"
NAMES=("Anna" "Bilal" "Cleo" "Dario" "Erika")
LANGS=("DE" "EN" "DE/EN" "EN" "DE")
FORMATS=("BP" "BP" "OPD" "egal" "BP")
ROLES=("S" "J" "SJ" "S" "S")
EXPS=(2 3 1 2 3)
for i in 0 1 2 3 4; do
  BTOK=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
  BODY=$(python3 -c "
import json, sys
print(json.dumps({
  'browser_token': '${BTOK}',
  'name': '${NAMES[$i]}',
  'language': '${LANGS[$i]}',
  'format': '${FORMATS[$i]}',
  'role': '${ROLES[$i]}',
  'could_speak_last': True,
  'experience': ${EXPS[$i]},
}))
")
  curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "${BODY}" "${BASE}/api/events/${CODE}/participants" >/dev/null
done

echo "→ GET /api/events/${CODE}/public"
PUB=$(curl -fsS "${BASE}/api/events/${CODE}/public")
COUNT=$(printf '%s' "${PUB}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["participant_count"])')
[ "${COUNT}" = "5" ] || { echo "expected count=5, got ${COUNT}"; exit 1; }
echo "  participant_count=${COUNT}"

echo "→ idempotent re-submit (same browser_token returns same row)"
SAMPLE_BTOK=$(curl -fsS "${BASE}/api/admin/${ADMIN_TOKEN}/state" \
  | python3 -c 'import json,sys; print("OK" if json.load(sys.stdin)["event"]["status"]=="open" else "X")')
[ "${SAMPLE_BTOK}" = "OK" ] || { echo "state status not open"; exit 1; }
BTOK=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
BODY=$(python3 -c "
import json
print(json.dumps({
  'browser_token': '${BTOK}',
  'name': 'Idempotent_Test',
  'language': 'EN', 'format': 'BP', 'role': 'S',
  'could_speak_last': True, 'experience': 2,
}))
")
FIRST=$(curl -fsS -X POST -H 'Content-Type: application/json' \
  -d "${BODY}" "${BASE}/api/events/${CODE}/participants")
SECOND=$(curl -fsS -X POST -H 'Content-Type: application/json' \
  -d "${BODY}" "${BASE}/api/events/${CODE}/participants")
[ "${FIRST}" = "${SECOND}" ] || { echo "idempotent retry returned different body"; exit 1; }
echo "  idempotent ok (same id on retry)"

echo "→ duplicate name (different browser_token) must 409"
BTOK2=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
BODY2=$(python3 -c "
import json
print(json.dumps({
  'browser_token': '${BTOK2}',
  'name': 'Anna',
  'language': 'DE', 'format': 'BP', 'role': 'S',
  'could_speak_last': True, 'experience': 2,
}))
")
DUP_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' -d "${BODY2}" \
  "${BASE}/api/events/${CODE}/participants")
[ "${DUP_CODE}" = "409" ] || { echo "expected 409, got ${DUP_CODE}"; exit 1; }
echo "  409 ok"

echo "→ POST /close-registration"
CLOSED=$(curl -fsS -X POST "${BASE}/api/admin/${ADMIN_TOKEN}/close-registration")
CSTATUS=$(printf '%s' "${CLOSED}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["event"]["status"])')
CCOUNT=$(printf '%s' "${CLOSED}" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["participants"]))')
[ "${CSTATUS}" = "closed" ] || { echo "expected status=closed, got ${CSTATUS}"; exit 1; }
[ "${CCOUNT}" = "6" ] || { echo "expected 6 participants, got ${CCOUNT}"; exit 1; }
echo "  status=${CSTATUS}  participants=${CCOUNT}"

echo "→ registration submit when closed must 409"
BTOK3=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
BODY3=$(python3 -c "
import json
print(json.dumps({
  'browser_token': '${BTOK3}',
  'name': 'TooLate',
  'language': 'EN', 'format': 'BP', 'role': 'S',
  'could_speak_last': True, 'experience': 2,
}))
")
LATE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' -d "${BODY3}" \
  "${BASE}/api/events/${CODE}/participants")
[ "${LATE}" = "409" ] || { echo "expected 409 after close, got ${LATE}"; exit 1; }
echo "  409 ok"

echo "→ POST /reopen-registration → status back to open"
REOPENED=$(curl -fsS -X POST "${BASE}/api/admin/${ADMIN_TOKEN}/reopen-registration")
RSTATUS=$(printf '%s' "${REOPENED}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["event"]["status"])')
[ "${RSTATUS}" = "open" ] || { echo "expected status=open, got ${RSTATUS}"; exit 1; }
echo "  status=${RSTATUS}"

echo "→ PATCH /event (room_limit=4, reg_deadline=…) "
PATCHED=$(curl -fsS -X PATCH -H 'Content-Type: application/json' \
  -d '{"room_limit": 4, "reg_deadline": 1800000000}' \
  "${BASE}/api/admin/${ADMIN_TOKEN}/event")
PRL=$(printf '%s' "${PATCHED}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["event"]["room_limit"])')
PRD=$(printf '%s' "${PATCHED}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["event"]["reg_deadline"])')
[ "${PRL}" = "4" ] || { echo "expected room_limit=4, got ${PRL}"; exit 1; }
[ "${PRD}" = "1800000000" ] || { echo "expected reg_deadline=1800000000, got ${PRD}"; exit 1; }
echo "  room_limit=${PRL}  reg_deadline=${PRD}"

echo "→ unknown admin_token must 404"
NOT_FOUND=$(curl -s -o /dev/null -w '%{http_code}' \
  "${BASE}/api/admin/000000000000000000000000deadbeef/state")
[ "${NOT_FOUND}" = "404" ] || { echo "expected 404, got ${NOT_FOUND}"; exit 1; }
echo "  404 ok"

echo
echo "ALL CHECKS PASSED"
