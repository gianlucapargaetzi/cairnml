from cairnml.backend_config.environment import EnvEntry


def test_a_legacy_name_still_works_and_is_reported_once(monkeypatch, capsys):
    entry = EnvEntry("CAIRNML_TEST_LEGACY_NAME", "TRAINS_TEST_LEGACY_NAME")
    monkeypatch.delenv("CAIRNML_TEST_LEGACY_NAME", raising=False)
    monkeypatch.setenv("TRAINS_TEST_LEGACY_NAME", "old")

    assert entry.get() == "old"
    assert entry.get() == "old"
    err = capsys.readouterr().err
    assert "TRAINS_TEST_LEGACY_NAME is deprecated" in err
    assert "use CAIRNML_TEST_LEGACY_NAME instead" in err
    assert err.count("deprecated") == 1


def test_the_current_name_wins_without_a_warning(monkeypatch, capsys):
    entry = EnvEntry("CAIRNML_TEST_CURRENT_NAME", "TRAINS_TEST_CURRENT_NAME")
    monkeypatch.setenv("CAIRNML_TEST_CURRENT_NAME", "new")
    monkeypatch.setenv("TRAINS_TEST_CURRENT_NAME", "old")

    assert entry.get() == "new"
    assert "deprecated" not in capsys.readouterr().err
