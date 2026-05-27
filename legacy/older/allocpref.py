import random

def generate_participants():
    n = random.randint(40, 45)
    out = []
    for i in range(1, n + 1):
        format_  = random.choices(["BP", "OPD", "egal"],            weights=[75, 15, 10])[0]
        language = random.choices(["Deutsch", "Englisch", "Bili"],  weights=[55, 30, 15])[0]
        role     = random.choices(["Speaker", "Judge", "SJ"],       weights=[65, 20, 15])[0]
        level    = random.choices(["Novice", "Intermediate", "Advanced"], weights=[35, 40, 25])[0]
        out.append([f"Teilnehmer_{i:02d}", format_, language, role, level])
    return out


# ====================== Hardgecodete Variablen ======================
PREFERRED_ROOM_SIZE = (9, 11)   # Wunschgröße
MIN_ROOM_SIZE       = 7         # ZWINGEND: kein Raum kleiner
MAX_ROOMS           = 5         # max. Anzahl Räume insgesamt
W_LANG              = 3         # Sprach-Mismatch wiegt schwerer ...
W_FMT               = 1         # ... als Format-Mismatch (Format = lockerste Regel)
BOTH_FORMATS_BONUS  = 4         # leichte Präferenz: beide Formate sollen vorkommen


def _room_penalty(size):
    lo, hi = PREFERRED_ROOM_SIZE
    if size < lo: return (lo - size) * 2.0
    if size > hi: return (size - hi) * 1.0
    return abs(size - (lo + hi) / 2) * 0.05

def _even(n, k):
    base, rem = divmod(n, k)
    return [base + 1] * rem + [base] * (k - rem)


# ---------- Schritt 2 (light): Leute auf eine gegebene Slate setzen ----------
def allocate(participants, rooms, w_lang=W_LANG, w_fmt=W_FMT):
    """Setzt jeden Teilnehmer in den Raum mit dem besten Match (Sprache wiegt schwerer
       als Format). Unflexible Leute zuerst, Joker (Bili/egal) füllen die Lücken."""
    cap  = {r[0]: r[3] for r in rooms}
    spec = {r[0]: (r[1], r[2]) for r in rooms}
    def fit(p, nr):
        l, f = spec[nr]
        return ((w_lang if (p[2] == "Bili" or l == p[2]) else 0)
                + (w_fmt if (p[1] == "egal" or f == p[1]) else 0))
    order = sorted(range(len(participants)),
                   key=lambda i: (participants[i][2] == "Bili") + (participants[i][1] == "egal"))
    assignment = {}
    for i in order:
        p = participants[i]
        _, nr = max((((fit(p, r[0]), cap[r[0]]), r[0]) for r in rooms if cap[r[0]] > 0),
                    key=lambda x: x[0])
        assignment[i] = nr
        cap[nr] -= 1
    return assignment

def _misses(participants, rooms):
    spec = {r[0]: (r[1], r[2]) for r in rooms}
    a = allocate(participants, rooms)
    ol = of = 0
    for i, p in enumerate(participants):
        rl, rf = spec[a[i]]
        if not (p[2] == "Bili" or rl == p[2]): ol += 1
        if not (p[1] == "egal" or rf == p[1]): of += 1
    return ol, of


# ====================== Räume vorschlagen (kleine Suche) ======================
def _compositions(R, parts=4):
    if parts == 1:
        yield (R,); return
    for i in range(R + 1):
        for rest in _compositions(R - i, parts - 1):
            yield (i,) + rest

_TYPES = [("Deutsch", "BP"), ("Deutsch", "OPD"), ("Englisch", "BP"), ("Englisch", "OPD")]

def suggest_rooms(participants):
    """Probiert alle sinnvollen Raum-Slates durch und nimmt die mit den geringsten
       gewichteten Mismatch-Kosten. Harte Regeln (>=7, keine Leftovers, <=MAX_ROOMS)
       sind durch die Kandidaten-Erzeugung garantiert."""
    N = len(participants)
    R_max = min(MAX_ROOMS, N // MIN_ROOM_SIZE)           # mehr Räume würden <7 erzwingen
    demand_fmts = {p[1] for p in participants if p[1] in ("BP", "OPD")}

    best = None
    for R in range(1, R_max + 1):
        sizes = sorted(_even(N, R), reverse=True)        # Größen summieren zu N, jede >=7
        for comp in _compositions(R, 4):                 # wie viele Räume je (Sprache,Format)?
            rooms, idx, nr = [], 0, 1
            for cnt, (lang, fmt) in zip(comp, _TYPES):
                for _ in range(cnt):
                    rooms.append([nr, lang, fmt, sizes[idx]]); idx += 1; nr += 1
            ol, of = _misses(participants, rooms)
            present = {r[2] for r in rooms}
            cost = (W_LANG * ol + W_FMT * of                         # Mismatches (Sprache > Format)
                    + BOTH_FORMATS_BONUS * len(demand_fmts - present)  # beide Formate anbieten
                    + 0.01 * sum(_room_penalty(r[3]) for r in rooms))  # Tie-Break: schöne Größen
            if best is None or cost < best[0]:
                best = (cost, rooms)

    rooms = best[1]
    for i, r in enumerate(rooms, 1):
        r[0] = i
    return rooms


# ====================== Zufriedenheits-Auswertung ======================
def satisfaction_report(participants, rooms, verbose=True):
    assignment = allocate(participants, rooms)
    spec = {r[0]: (r[1], r[2]) for r in rooms}
    off_lang, off_fmt, happy = [], [], 0
    for i, p in enumerate(participants):
        rl, rf = spec[assignment[i]]
        bad_l = not (p[2] == "Bili" or rl == p[2])       # Bili ist immer sprach-zufrieden
        bad_f = not (p[1] == "egal" or rf == p[1])       # egal ist immer format-zufrieden
        if bad_l: off_lang.append((p, rl))
        if bad_f: off_fmt.append((p, rf))
        if not bad_l and not bad_f: happy += 1
    n = len(participants)
    if verbose:
        print(f"Zufrieden: {happy}/{n}  ({100 * happy / n:.0f}%)")
        print(f"  außerhalb der Sprach-Präferenz: {len(off_lang)}")
        print(f"  außerhalb der Format-Präferenz: {len(off_fmt)}")
        for p, got in off_fmt:
            print(f"    - {p[0]}: wollte {p[1]}, sitzt in {got}-Raum")
        for p, got in off_lang:
            print(f"    - {p[0]}: wollte {p[2]}, sitzt in {got}-Raum")
    return {"total": n, "satisfied": happy,
            "off_language": len(off_lang), "off_format": len(off_fmt)}


# ====================== Demo ======================
if __name__ == "__main__":
    participants = generate_participants()
    rooms = suggest_rooms(participants)
    print(f"{len(participants)} Teilnehmer  ->  {len(rooms)} Räume\n")
    for nr, lang, fmt, n in rooms:
        print(f"  Raum {nr}: {lang:<9}{fmt:<5}{n} Personen")
    print()
    satisfaction_report(participants, rooms)