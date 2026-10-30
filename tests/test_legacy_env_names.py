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


def test_the_environment_name_is_read_with_its_legacy_fallback(monkeypatch, capsys):
    from cairnml.backend_config.environment import read_env_with_legacy

    monkeypatch.delenv("CAIRNML_TEST_ENV_NAME", raising=False)
    monkeypatch.setenv("TRAINS_TEST_ENV_NAME", "legacy")

    assert read_env_with_legacy("CAIRNML_TEST_ENV_NAME", "TRAINS_TEST_ENV_NAME") == "legacy"
    assert read_env_with_legacy("CAIRNML_TEST_ENV_NAME", "TRAINS_TEST_ENV_NAME") == "legacy"
    err = capsys.readouterr().err
    assert err.count("TRAINS_TEST_ENV_NAME is deprecated") == 1


def test_the_current_environment_name_is_preferred_without_a_warning(monkeypatch, capsys):
    from cairnml.backend_config.environment import read_env_with_legacy

    monkeypatch.setenv("CAIRNML_TEST_ENV_NAME", "current")
    monkeypatch.setenv("TRAINS_TEST_ENV_NAME", "legacy")

    assert read_env_with_legacy("CAIRNML_TEST_ENV_NAME", "TRAINS_TEST_ENV_NAME") == "current"
    assert "deprecated" not in capsys.readouterr().err


def test_the_trains_config_file_warns_once_and_cairnml_conf_does_not(capsys):
    from cairnml.backend_config.environment import check_legacy_config_file

    check_legacy_config_file("/home/someone/trains.conf")
    check_legacy_config_file("/home/someone/trains.conf")
    err = capsys.readouterr().err
    assert err.count("trains.conf is deprecated") == 1
    assert "rename it to cairnml.conf" in err

    check_legacy_config_file("/home/someone/cairnml.conf")
    assert "cairnml.conf" not in capsys.readouterr().err
