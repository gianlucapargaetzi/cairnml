from cairnml.cli.config.__main__ import write_config


def test_the_written_config_names_the_device_and_holds_no_credentials(tmp_path):
    conf = tmp_path / "cairnml.conf"
    write_config(conf, "https://api.example", "https://app.example", "dev_1")

    text = conf.read_text()
    assert "access_key" not in text
    assert "secret_key" not in text
    assert "credentials {" not in text  # the API credentials block

    from cairnml.utilities.pyhocon import ConfigFactory
    parsed = ConfigFactory.parse_string(text)
    assert parsed.get("api.api_server") == "https://api.example"
    assert parsed.get("api.web_server") == "https://app.example"
    assert parsed.get("api.idp.device_id") == "dev_1"
