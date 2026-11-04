from cairnml.backend_config import environment


def test_no_warning_when_the_legacy_file_does_not_exist(tmp_path, capsys):
    environment._warned_legacy_keys.discard("trains.conf")
    environment.check_legacy_config_file(str(tmp_path / "trains.conf"))
    assert capsys.readouterr().err == ""


def test_warning_once_when_the_legacy_file_exists(tmp_path, capsys):
    environment._warned_legacy_keys.discard("trains.conf")
    path = tmp_path / "trains.conf"
    path.write_text("")
    environment.check_legacy_config_file(str(path))
    environment.check_legacy_config_file(str(path))
    assert capsys.readouterr().err.count("trains.conf is deprecated") == 1
