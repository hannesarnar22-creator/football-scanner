import json

import pytest

from scanner.teams import MatchMethod, TeamRegistry, default_registry, normalize_name

R = default_registry()


@pytest.mark.parametrize(
    "name",
    ["Manchester United", "Man Utd", "Manchester U.", "MANCHESTER UTD FC", "Man United", "manchester united"],
)
def test_manchester_united_variants(name):
    r = R.resolve_team(name, "ENG-PL")
    assert r.team is not None and r.team.id == "MUN"
    assert r.method is MatchMethod.EXACT


@pytest.mark.parametrize(
    "name,comp,team_id",
    [
        ("Breidablik", "ISL-BD", "BRE"),
        ("Breiðablik", "ISL-BD", "BRE"),
        ("Vikingur Reykjavik", "ISL-BD", "VIK"),
        ("Bayern Munich", "GER-BL", "BAY"),
        ("Borussia Monchengladbach", "GER-BL", "BMG"),
        ("Atletico de Madrid", "ESP-LL", "ATM"),
        ("AC Milan", "ITA-SA", "MIL"),
        ("Brighton and Hove Albion", "ENG-PL", "BHA"),
    ],
)
def test_aliases_and_accents(name, comp, team_id):
    r = R.resolve_team(name, comp)
    assert r.team is not None and r.team.id == team_id


def test_normalize_name():
    assert normalize_name("Manchester United FC") == "manchester united"
    assert normalize_name("Breiðablik") == "breidablik"
    assert normalize_name("Brighton & Hove Albion") == "brighton and hove albion"
    assert normalize_name("  Atlético   Madrid ") == "atletico madrid"


def test_team_code_is_used():
    r = R.resolve_team("Some Unusual Name", "ENG-PL", code="MUN")
    assert r.team is not None and r.team.id == "MUN"
    assert r.method is MatchMethod.CODE


def test_code_conflicting_with_name_is_not_trusted():
    r = R.resolve_team("Liverpool", "ENG-PL", code="MUN")
    assert not r.accepted
    assert r.method is MatchMethod.UNCERTAIN


def test_code_from_other_competition_ignored():
    # MUN er í ENG-PL; í La Liga á kóðinn ekki við
    r = R.resolve_team("Real Madrid", "ESP-LL", code="MUN")
    assert r.team is not None and r.team.id == "RMA"


def test_competition_restricts_matches():
    # Real Madrid er ekki í Premier League
    assert not R.resolve_team("Real Madrid", "ENG-PL").accepted


def test_confident_fuzzy_accepted_but_flagged():
    r = R.resolve_team("Manchestr United", "ENG-PL")
    assert r.team is not None and r.team.id == "MUN"
    assert r.method is MatchMethod.FUZZY
    assert r.is_fuzzy


@pytest.mark.parametrize("name,comp", [("Man", "ENG-PL"), ("Real", "ESP-LL"), ("Utd", "ENG-PL"), ("Borussia", "GER-BL")])
def test_uncertain_fuzzy_not_accepted(name, comp):
    r = R.resolve_team(name, comp)
    assert not r.accepted
    assert r.method in (MatchMethod.UNCERTAIN, MatchMethod.NOT_FOUND)


def test_unknown_team_not_found():
    r = R.resolve_team("Xyzzy Rovers", "ENG-PL")
    assert not r.accepted


def test_competition_aliases_exact_only():
    assert R.resolve_competition("EPL").id == "ENG-PL"
    assert R.resolve_competition("England - Premier League").id == "ENG-PL"
    assert R.resolve_competition("Iceland - Besta deild").id == "ISL-BD"
    assert R.resolve_competition("Premier Leag") is None


def test_ambiguous_alias_file_rejected(tmp_path):
    data = {
        "competitions": [{"id": "C", "name": "Comp", "aliases": []}],
        "teams": [
            {"id": "A", "competition": "C", "name": "United", "aliases": []},
            {"id": "B", "competition": "C", "name": "Other", "aliases": ["United FC"]},
        ],
    }
    path = tmp_path / "teams.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Tvírætt"):
        TeamRegistry.from_file(path)
