import random


def generate_participants(n: int | None = None) -> list[list]:
    """Generiert eine Liste von Teilnehmern gemäß dem Schema aus problem.md.

    Schema pro Teilnehmer:
        [Name, Sprache, Format, Rolle, konnte_letztes_mal_sprechen, Erfahrung]
        - Sprache: "DE" | "EN" | "DE/EN"
        - Format:  "BP" | "OPD" | "egal"
        - Rolle:   "S"  | "J"   | "SJ"
        - konnte_letztes_mal_sprechen: bool (nur False, wenn zuletzt zum Judgen gezwungen)
        - Erfahrung: "1" (Anfänger) | "2" (Intermediate) | "3" (Fortgeschritten)
    """
    if n is None:
        n = random.randint(30, 40)

    participants = []
    for i in range(1, n + 1):
        language = random.choices(
            ["DE", "EN", "DE/EN"],
            weights=[25, 50, 25],
        )[0]

        format_ = random.choices(
            ["BP", "OPD", "egal"],
            weights=[75, 15, 10],
        )[0]
        # OPD ist im deutschsprachigen Raum überwiegend deutsch
        if format_ == "OPD":
            language = random.choices(["EN", "DE/EN"], weights=[75, 25])[0]

        role = random.choices(
            ["S", "J", "SJ"],
            weights=[65, 20, 15],
        )[0]

        # Die meisten konnten letztes Mal sprechen; nur wenige wurden zum Judgen gezwungen
        could_speak_last = random.choices([True, False], weights=[95, 5])[0]

        experience = random.choices(
            ["1", "2", "3"],
            weights=[35, 40, 25],
        )[0]

        participants.append([
            f"Teilnehmer_{i:02d}",
            language,
            format_,
            role,
            could_speak_last,
            experience,
        ])

    return participants


if __name__ == "__main__":
    for p in generate_participants():
        print(p)
