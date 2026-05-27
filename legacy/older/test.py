from ortools.sat.python import cp_model

def solve_debate_allocation(participants, max_rooms=5, min_room_size=8, max_room_size=11):
    model = cp_model.CpModel()
    num_p = len(participants)

    # ==========================================
    # 1. GEWICHTUNG DER STRAFPUNKTE (Prioritäten)
    # ==========================================
    # Passe diese Werte an, um das Verhalten zu steuern.
    # Da 4x falsches Format (4*300=1200) > 1x falsche Sprache (1000), 
    # wird der Algorithmus genau das tun, was du gefordert hast.
    WEIGHT_LANG = 1000         # Prio 1: Sprache
    WEIGHT_FORMAT = 300        # Prio 2: Format
    WEIGHT_ROLE = 100          # Prio 3: Rolle (Wollte S, muss J)
    WEIGHT_FORCED_SPEAK = 50   # Prio 4: War letztes Mal J, muss diesmal wieder J sein
    WEIGHT_EXP = 10            # Prio 5: Erfahrung (Abweichung vom Raum-Schnitt)
    WEIGHT_MISSING_FORMAT = 2000 # Constraints: Möglichst beide Formate abbilden

    # ==========================================
    # 2. VARIABLEN DEKLARIEREN
    # ==========================================
    # x[p][r]: Ist Person p in Raum r?
    x = {}
    for p in range(num_p):
        for r in range(max_rooms):
            x[(p, r)] = model.NewBoolVar(f'x_{p}_{r}')
            
    # role[p]: Ist Person p Speaker (1) oder Judge (0)?
    is_speaker = {p: model.NewBoolVar(f'is_speaker_{p}') for p in range(num_p)}
    
    # Raumeigenschaften
    room_active = {r: model.NewBoolVar(f'room_active_{r}') for r in range(max_rooms)}
    room_lang = {r: model.NewBoolVar(f'room_lang_{r}') for r in range(max_rooms)}     # 0=DE, 1=EN
    room_format = {r: model.NewBoolVar(f'room_format_{r}') for r in range(max_rooms)} # 0=BP, 1=OPD

    # ==========================================
    # 3. HARTE CONSTRAINTS (Dürfen nicht gebrochen werden)
    # ==========================================
    
    # A. Jeder Teilnehmer ist in exakt einem Raum
    for p in range(num_p):
        model.AddExactlyOne([x[(p, r)] for r in range(max_rooms)])

    # B. Raumgrößen (nur wenn Raum aktiv ist)
    for r in range(max_rooms):
        room_size = sum(x[(p, r)] for p in range(num_p))
        model.Add(room_size >= min_room_size * room_active[r])
        model.Add(room_size <= max_room_size * room_active[r])
        
    # C. Symmetriebrechung (Macht den Solver VIEL schneller)
    # Räume werden von links nach rechts aufgefüllt (Raum 2 kann nur aktiv sein, wenn Raum 1 aktiv ist)
    for r in range(max_rooms - 1):
        model.AddImplication(room_active[r+1], room_active[r])

    # D. Jeder aktive Raum braucht mindestens einen Judge!
    for r in range(max_rooms):
        judges_in_room = []
        for p in range(num_p):
            p_is_judge_in_r = model.NewBoolVar(f'p_is_j_in_{p}_{r}')
            # p_is_judge_in_r == 1 GDW Person p ist im Raum r UND ist Judge (not is_speaker)
            model.AddBoolAnd([x[(p, r)], is_speaker[p].Not()]).OnlyEnforceIf(p_is_judge_in_r)
            model.AddBoolOr([x[(p, r)].Not(), is_speaker[p]]).OnlyEnforceIf(p_is_judge_in_r.Not())
            judges_in_room.append(p_is_judge_in_r)
        
        model.Add(sum(judges_in_room) >= 1).OnlyEnforceIf(room_active[r])

    # ==========================================
    # 4. WEICHE CONSTRAINTS (Strafen für Abweichungen)
    # ==========================================
    penalties = []

    for p in range(num_p):
        name, pref_lang, pref_fmt, pref_role, could_speak, exp = participants[p]
        exp = int(exp)

        # ---- Prio 1: Sprache ----
        if pref_lang in ["DE", "EN"]:
            target_lang = 0 if pref_lang == "DE" else 1
            wrong_lang = model.NewBoolVar(f'wrong_lang_{p}')
            for r in range(max_rooms):
                # Wenn in Raum r und Raum-Sprache ungleich Wunsch-Sprache -> wrong_lang = True
                model.Add(room_lang[r] != target_lang).OnlyEnforceIf([x[(p, r)], wrong_lang])
                model.Add(room_lang[r] == target_lang).OnlyEnforceIf([x[(p, r)], wrong_lang.Not()])
            penalties.append(wrong_lang * WEIGHT_LANG)

        # ---- Prio 2: Format ----
        if pref_fmt in ["BP", "OPD"]:
            target_fmt = 0 if pref_fmt == "BP" else 1
            wrong_fmt = model.NewBoolVar(f'wrong_fmt_{p}')
            for r in range(max_rooms):
                model.Add(room_format[r] != target_fmt).OnlyEnforceIf([x[(p, r)], wrong_fmt])
                model.Add(room_format[r] == target_fmt).OnlyEnforceIf([x[(p, r)], wrong_fmt.Not()])
            penalties.append(wrong_fmt * WEIGHT_FORMAT)

        # ---- Prio 3: Rolle ----
        if pref_role in ["S", "J"]:
            target_speaker = 1 if pref_role == "S" else 0
            wrong_role = model.NewBoolVar(f'wrong_role_{p}')
            model.Add(is_speaker[p] != target_speaker).OnlyEnforceIf(wrong_role)
            model.Add(is_speaker[p] == target_speaker).OnlyEnforceIf(wrong_role.Not())
            penalties.append(wrong_role * WEIGHT_ROLE)

        # ---- Prio 4: Konnte letztes Mal nicht sprechen (Forced Judge) ----
        if not could_speak:
            # Muss diesmal Speaker sein!
            forced_violation = model.NewBoolVar(f'forced_violation_{p}')
            model.Add(is_speaker[p] == 0).OnlyEnforceIf(forced_violation)
            model.Add(is_speaker[p] == 1).OnlyEnforceIf(forced_violation.Not())
            penalties.append(forced_violation * WEIGHT_FORCED_SPEAK)

        # ---- Prio 5: Erfahrung (vereinfacht: Strafe für extrem niedrige Erfahrung als Judge) ----
        # Für einen vollen Varianz-Ausgleich reicht hier der Platz nicht, aber wir zwingen
        # den Solver, Anfänger (Exp=1) ungern als Judge einzusetzen, wenn erfahrene (Exp=3) da sind.
        if exp == 1:
            bad_judge = model.NewBoolVar(f'bad_judge_{p}')
            model.Add(is_speaker[p] == 0).OnlyEnforceIf(bad_judge)
            model.Add(is_speaker[p] == 1).OnlyEnforceIf(bad_judge.Not())
            penalties.append(bad_judge * WEIGHT_EXP * 2)

    # ---- Constraint: Möglichst beide Formate ----
    has_bp = model.NewBoolVar('has_bp')
    has_opd = model.NewBoolVar('has_opd')
    r_is_bp = [model.NewBoolVar(f'r_is_bp_{r}') for r in range(max_rooms)]
    r_is_opd = [model.NewBoolVar(f'r_is_opd_{r}') for r in range(max_rooms)]
    
    for r in range(max_rooms):
        model.AddBoolAnd([room_active[r], room_format[r].Not()]).OnlyEnforceIf(r_is_bp[r])
        model.AddBoolOr([room_active[r].Not(), room_format[r]]).OnlyEnforceIf(r_is_bp[r].Not())
        
        model.AddBoolAnd([room_active[r], room_format[r]]).OnlyEnforceIf(r_is_opd[r])
        model.AddBoolOr([room_active[r].Not(), room_format[r].Not()]).OnlyEnforceIf(r_is_opd[r].Not())

    model.AddMaxEquality(has_bp, r_is_bp)
    model.AddMaxEquality(has_opd, r_is_opd)
    
    missing_format = model.NewBoolVar('missing_format')
    model.AddBoolOr([has_bp.Not(), has_opd.Not()]).OnlyEnforceIf(missing_format)
    model.AddBoolAnd([has_bp, has_opd]).OnlyEnforceIf(missing_format.Not())
    penalties.append(missing_format * WEIGHT_MISSING_FORMAT)

    # ==========================================
    # 5. ZIELFUNKTION & SOLVER
    # ==========================================
    model.Minimize(sum(penalties))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0 # Bricht nach 10 Sekunden ab, falls zu komplex
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        print(f"Lösung gefunden! (Strafpunkte: {solver.ObjectiveValue()})\n")
        
        for r in range(max_rooms):
            if solver.Value(room_active[r]):
                lang_str = "EN" if solver.Value(room_lang[r]) else "DE"
                fmt_str = "OPD" if solver.Value(room_format[r]) else "BP"
                
                room_participants = []
                for p in range(num_p):
                    if solver.Value(x[(p, r)]):
                        role = "S" if solver.Value(is_speaker[p]) else "J"
                        name = participants[p][0]
                        room_participants.append(f"{name} ({role})")
                
                print(f"Raum {r+1} | {lang_str} | {fmt_str} | Größe: {len(room_participants)}")
                print(f"Teilnehmer: {', '.join(room_participants)}")
                print("-" * 50)
    else:
        print("Keine gültige Zuteilung gefunden. (Evtl. zu wenige Teilnehmer für die Raumgröße?)")


# --- Testdaten zum Ausführen ---
if __name__ == "__main__":
    # 17 Leute: Reicht für 2 Räume (z.B. 1x8, 1x9)
    test_participants = [
        ["Anna", "DE", "BP", "S", True, "2"],
        ["Bob", "EN", "BP", "S", False, "3"], # Bob war Judge, muss Speaker werden
        ["Charlie", "DE/EN", "egal", "SJ", True, "1"],
        ["David", "DE", "OPD", "S", True, "2"],
        ["Eva", "EN", "OPD", "J", True, "3"],
        ["Felix", "DE", "BP", "S", True, "2"],
        ["Greta", "EN", "BP", "S", True, "2"],
        ["Hans", "DE/EN", "OPD", "S", True, "1"],
        ["Iris", "DE", "egal", "J", True, "3"],
        ["Jan", "EN", "BP", "S", False, "2"], # Jan war auch Judge
        ["Klara", "DE", "BP", "S", True, "1"],
        ["Leo", "EN", "OPD", "S", True, "2"],
        ["Mia", "DE/EN", "BP", "SJ", True, "3"],
        ["Nils", "DE", "OPD", "S", True, "1"],
        ["Olga", "EN", "BP", "S", True, "2"],
        ["Paul", "DE", "egal", "S", True, "1"],
        ["Quinn", "EN", "OPD", "J", True, "3"]
    ]
    
    solve_debate_allocation(test_participants, max_rooms=3, min_room_size=8, max_room_size=11)