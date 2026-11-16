"""An application can give the SDK's configuration its own local files and environment values through two methods."""

from pyhocon import ConfigFactory

from cairnml.backend_config.config import Config
from cairnml.backend_config.defs import LOCAL_CONFIG_FILES


class AppConfig(Config):
    def __init__(self, path, **kwargs):
        self._path = path
        super().__init__(**kwargs)

    def local_config_files(self):
        return [str(self._path)]

    def extra_env_config_values(self):
        return ConfigFactory.parse_string("api.from_env: yes")


def test_the_sdk_reads_its_own_local_files_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("CAIRNML_CONFIG_FILE", raising=False)
    config = Config(config_folder=str(tmp_path))
    assert config.local_config_files() == list(LOCAL_CONFIG_FILES)
    assert config.extra_env_config_values() is None


def test_an_application_reads_its_own_file_and_its_environment_values(tmp_path):
    app_file = tmp_path / "app.conf"
    app_file.write_text('api.server: "https://app.example"\n')
    config = AppConfig(app_file, config_folder=str(tmp_path / "none"))
    config.reload()
    assert config.get("api.server") == "https://app.example"
    assert config.get("api.from_env") == "yes"
