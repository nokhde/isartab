import random

def generate_participants():
    n = random.randint(30, 40)
    out = []
    for i in range(1, n + 1):
        format_  = random.choices(["BP", "OPD", "egal"],            weights=[75, 20, 5])[0]
        language = random.choices(["Deutsch", "Englisch", "Bili"],  weights=[55, 30, 15])[0]
        if format == "OPD": language = random.choices(["egal", "Englisch"], weights=[20, 80])[0]
        role     = random.choices(["Speaker", "Judge", "SJ"],       weights=[65, 20, 15])[0]
        level    = random.choices(["Novice", "Intermediate", "Advanced"], weights=[35, 40, 25])[0]
        out.append([f"Teilnehmer_{i:02d}", format_, language, role, level])
    return out


# ====================== Hardgecodete Variablen ======================
PREFERRED_ROOM_SIZE = (9, 12)   # Wunschgröße
MIN_ROOM_SIZE       = 8         # ZWINGEND: kein Raum kleiner
MAX_ROOMS           = 5         # max. Anzahl Räume insgesamt
UNDERFLOW_WEIGHT    = 2.0       # zu klein doppelt so schlimm wie ...
OVERFLOW_WEIGHT     = 1.0       # ... zu groß


# ---------- kleine Helfer ----------
def _room_penalty(size):
    lo, hi = PREFERRED_ROOM_SIZE
    if size < lo: return (lo - size) * UNDERFLOW_WEIGHT
    if size > hi: return (size - hi) * OVERFLOW_WEIGHT
    return abs(size - (lo + hi) / 2) * 0.05

def _even(n, k):
    """n Personen so gleichmäßig wie möglich auf k Räume (jeder >= 7, falls k <= n//7)."""
    base, rem = divmod(n, k)
    return [base + 1] * rem + [base] * (k - rem)

def _pool_pen(n, k):
    return sum(_room_penalty(s) for s in _even(n, k))

def _best_pool_pen(n):
    """Beste erreichbare Strafe – Pools < 7 werden teuer, damit Bili sie auffüllt."""
    if n == 0: return 0.0
    if n < MIN_ROOM_SIZE: return _room_penalty(n)
    return min(_pool_pen(n, k) for k in range(1, n // MIN_ROOM_SIZE + 1))

def _fmt_counts(people):
    bp  = sum(p[1] == "BP"   for p in people)
    opd = sum(p[1] == "OPD"  for p in people)
    eg  = sum(p[1] == "egal" for p in people)
    return bp, opd, eg

def _label_formats(sizes, bp, opd, eg):
    """Räume eines Sprach-Pools mit Format labeln. OPD bekommt nur dann einen eigenen
       Raum, wenn genug OPD-Leute (inkl. egal) ihn auf >=7 füllen; sonst fällt die
       Minderheit in die BP-Räume (Format ist die lockerste Regel)."""
    k, s = len(sizes), bp + opd + eg
    if s == 0:   return []
    if opd == 0: return ["BP"] * k
    if bp == 0:  return ["OPD"] * k
    o = round(k * (opd + eg / 2) / s)               # OPD-Räume nach Nachfrage
    o = min(o, (opd + eg) // MIN_ROOM_SIZE)         # nur was OPD wirklich füllen kann
    o = max(0, min(o, k - 1))                       # BP behält mind. 1 Raum
    while o > 0 and bp > sum(sorted(sizes)[o:]):    # BP-Rigid muss in die BP-Räume passen
        o -= 1
    labels = ["BP"] * k
    for i in sorted(range(k), key=lambda i: sizes[i])[:o]:   # kleinste Räume -> OPD
        labels[i] = "OPD"
    return labels


# ====================== Hauptfunktion: Räume vorschlagen ======================
def suggest_rooms(participants):
    N = len(participants)
    de   = [p for p in participants if p[2] == "Deutsch"]
    en   = [p for p in participants if p[2] == "Englisch"]
    bili = [p for p in participants if p[2] == "Bili"]

    # 1. SPRACHE (wichtigste Teilung): Bili ausbalancieren; kleine Pools werden zuerst gefüllt
    for p in bili:
        if (_best_pool_pen(len(de) + 1) + _best_pool_pen(len(en))
                <= _best_pool_pen(len(de)) + _best_pool_pen(len(en) + 1)):
            de.append(p)
        else:
            en.append(p)

    pools = []
    if de: pools.append(["Deutsch", de])
    if en: pools.append(["Englisch", en])

    # Sprache nur als ALLERLETZTES Mittel mischen (Pool < 7 nicht auffüllbar, oder MAX_ROOMS zu klein)
    while len(pools) > 1 and any(len(pl[1]) < MIN_ROOM_SIZE for pl in pools):
        pools.sort(key=lambda pl: len(pl[1]))
        small = pools.pop(0)
        pools[0][1] = pools[0][1] + small[1]
        pools[0][0] = "Gemischt"
    while len(pools) > MAX_ROOMS:
        pools.sort(key=lambda pl: len(pl[1]))
        small = pools.pop(0)
        pools[0][1] = pools[0][1] + small[1]
        pools[0][0] = "Gemischt"

    pool_sizes = [len(pl[1]) for pl in pools]
    caps = [sz // MIN_ROOM_SIZE for sz in pool_sizes]      # max. Räume je Pool (hält >=7)

    # 2. Gesamtraumzahl R bestimmen (Annäherung an N/Wunschgröße, durch alle Limits gedeckelt)
    mid = (PREFERRED_ROOM_SIZE[0] + PREFERRED_ROOM_SIZE[1]) / 2
    R = min(max(round(N / mid), len(pools)), MAX_ROOMS, sum(caps))
    R = max(R, len(pools))                                 # jeder Pool kriegt mind. 1 Raum

    # ... und auf die Pools verteilen: bei 1 anfangen, Rest dahin, wo es am meisten bringt
    r = [1] * len(pools)
    remaining = R - len(pools)
    while remaining > 0:
        best_i, best_gain = None, None
        for i in range(len(pools)):
            if r[i] < caps[i]:
                gain = _pool_pen(pool_sizes[i], r[i]) - _pool_pen(pool_sizes[i], r[i] + 1)
                if best_i is None or gain > best_gain:
                    best_i, best_gain = i, gain
        if best_i is None: break
        r[best_i] += 1
        remaining -= 1

    # 3. Räume bauen (Größen gleichmäßig, Format so rein wie möglich)
    rooms, nr = [], 1
    for (label, people), k in zip(pools, r):
        sizes = _even(len(people), k)
        bp, opd, eg = _fmt_counts(people)
        for sz, fmt in zip(sizes, _label_formats(sizes, bp, opd, eg)):
            rooms.append([nr, label, fmt, sz])
            nr += 1
    return rooms

# ====================== Schritt 2 (light): Leute auf Räume setzen ======================
def allocate(participants, rooms):
    """Setzt jeden Teilnehmer in einen Raum. Unflexible Leute zuerst, damit sie ihren
       passenden Raum bekommen; Joker (Bili/egal) füllen die Lücken. Gibt {index: raum_nr}."""
    cap  = {r[0]: r[3] for r in rooms}
    spec = {r[0]: (r[1], r[2]) for r in rooms}          # raum_nr -> (sprache, format)

    def lang_ok(p, nr): return p[2] == "Bili" or spec[nr][0] in (p[2], "Gemischt")
    def fmt_ok(p, nr):  return p[1] == "egal" or spec[nr][1] in (p[1], "Gemischt")
    def score(p, nr):   return (2 if lang_ok(p, nr) else 0) + (1 if fmt_ok(p, nr) else 0)

    flexibility = lambda p: (p[2] == "Bili") + (p[1] == "egal")   # 0 = ganz festgelegt
    order = sorted(range(len(participants)), key=lambda i: flexibility(participants[i]))

    assignment = {}
    for i in order:
        p = participants[i]
        # Raum mit freiem Platz, der am besten passt (Sprache wiegt schwerer als Format)
        _, nr = max(((( score(p, r[0]), cap[r[0]] ), r[0]) for r in rooms if cap[r[0]] > 0),
                    key=lambda x: x[0])
        assignment[i] = nr
        cap[nr] -= 1
    return assignment


def satisfaction_report(participants, rooms, verbose=True):
    """Wertet aus, wie viele Leute innerhalb ihrer Präferenzen gelandet sind."""
    assignment = allocate(participants, rooms)
    spec = {r[0]: (r[1], r[2]) for r in rooms}

    off_lang, off_fmt, happy = [], [], 0
    for i, p in enumerate(participants):
        rl, rf = spec[assignment[i]]
        bad_l = not (p[2] == "Bili" or rl in (p[2], "Gemischt"))     # Bili ist immer zufrieden
        bad_f = not (p[1] == "egal" or rf in (p[1], "Gemischt"))     # egal ist immer zufrieden
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
    avg_satisfaction = 0.0
    for i in range(100):
        
        participants = generate_participants()
        rooms = suggest_rooms(participants)

        print(f"{len(participants)} Teilnehmer  ->  {len(rooms)} Räume (max. {MAX_ROOMS})\n")
        print(f"{'Raum':<5}{'Sprache':<10}{'Format':<8}{'Anzahl':<7}")
        for nr, lang, fmt, n in rooms:
            print(f"{nr:<5}{lang:<10}{fmt:<8}{n:<7}")
        print(f"\nSumme: {sum(r[3] for r in rooms)} (keine Leftovers), kleinster Raum: {min(r[3] for r in rooms)}")

        print(satisfaction_report(participants, rooms)["satisfied"]/len(participants))
        avg_satisfaction += satisfaction_report(participants, rooms, verbose=False)["satisfied"]/len(participants)
    print(f"\nDurchschnittliche Zufriedenheit über 100 Läufe: {avg_satisfaction/100:.2f}")



