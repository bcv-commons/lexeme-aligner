from lexeme_aligner import onboard_catalog as oc


def test_stops_before_a_language_when_disk_is_low(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(oc, "build_plan", lambda include_dbt: [{"iso": "aaa"}, {"iso": "bbb"}])
    monkeypatch.setattr(oc, "run_one", lambda lang, full=False: (ran.append(lang["iso"]) or (True, "ok")))
    monkeypatch.setattr(oc, "free_gb", lambda *a: 3.0)
    monkeypatch.setattr("sys.argv", ["onboard_catalog", "--full"])
    assert oc.main() == 2
    assert ran == []
    assert "STOP before 'aaa'" in capsys.readouterr().err


def test_runs_everything_when_disk_is_fine(monkeypatch):
    ran = []
    monkeypatch.setattr(oc, "build_plan", lambda include_dbt: [{"iso": "aaa"}, {"iso": "bbb"}])
    monkeypatch.setattr(oc, "run_one", lambda lang, full=False: (ran.append(lang["iso"]) or (True, "ok")))
    monkeypatch.setattr(oc, "free_gb", lambda *a: 100.0)
    monkeypatch.setattr("sys.argv", ["onboard_catalog", "--full"])
    assert oc.main() == 0
    assert ran == ["aaa", "bbb"]
