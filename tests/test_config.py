from pathlib import Path

import pytest

from plotroom.app.config import (
    DEFAULT_ALLOWED_HOSTS,
    DEFAULT_LOG_BACKUPS,
    DEFAULT_LOG_MAX_BYTES,
    ConfigError,
    load_config,
)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_file_values_and_registry(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        'data_dir = "state"\nhost = "0.0.0.0"\nport = 9000\n'
        'allowed_hosts = ["plotroom.example.org"]\n[projects.alpha]\n[projects.beta-2]\n',
    )
    config = load_config(path, {})
    assert config.data_dir == tmp_path / "state"
    assert config.host == "0.0.0.0"
    assert config.port == 9000
    assert config.allowed_hosts == ("plotroom.example.org",)
    assert config.projects == ("alpha", "beta-2")


def test_defaults_are_loopback(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, "[projects.alpha]\n"), {})
    assert config.host == "127.0.0.1"
    assert config.port == 8765
    assert config.allowed_hosts == DEFAULT_ALLOWED_HOSTS
    assert config.url == "http://127.0.0.1:8765/mcp"


def test_action_routes_are_project_local_and_independent_of_delivery(tmp_path: Path) -> None:
    config = load_config(
        write(
            tmp_path,
            '[projects.alpha.routes]\npublish = "future-lane"\n"release.sign" = "audit"\n'
            '[projects.beta.routes]\npublish = "other-lane"\n',
        ),
        {},
    )
    assert config.routes == {
        "alpha": {"publish": "future-lane", "release.sign": "audit"},
        "beta": {"publish": "other-lane"},
    }
    assert config.delivery_routes == {"alpha": {}, "beta": {}}


@pytest.mark.parametrize(
    "settings",
    [
        'routes = "lane"',
        "routes = { publish = true }",
        'routes = { publish = "  " }',
        'routes = { " " = "lane" }',
        f'routes = {{ publish = "{"x" * 129}" }}',
    ],
)
def test_action_route_config_validates_kind_and_lane(tmp_path: Path, settings: str) -> None:
    with pytest.raises(ConfigError, match=r"projects\.alpha\.routes"):
        load_config(write(tmp_path, f"[projects.alpha]\n{settings}\n"), {})


@pytest.mark.parametrize("board", ["project", "team-", "team_"])
def test_switchboard_requires_canonical_explicit_board(tmp_path: Path, board: str) -> None:
    config = write(tmp_path, f'[projects.alpha.switchboard]\nboard = "{board}"\n')
    with pytest.raises(ConfigError, match="canonical relay name"):
        load_config(config, {})


def test_environment_overrides_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, 'port = 9000\ndata_dir = "from-file"\n[projects.alpha]\n')
    env = {
        "PLOTROOM_PORT": "9100",
        "PLOTROOM_DATA_DIR": str(tmp_path / "from-env"),
        "PLOTROOM_HOST": "::1",
        "PLOTROOM_ALLOWED_HOSTS": "a.example, b.example",
        "PLOTROOM_PROJECTS": "x,y",
    }
    config = load_config(path, env)
    assert config.port == 9100
    assert config.data_dir == tmp_path / "from-env"
    assert config.host == "::1"
    assert config.url == "http://[::1]:9100/mcp"
    assert config.allowed_hosts == ("a.example", "b.example")
    assert config.projects == ("x", "y")


def test_a_missing_explicit_config_file_is_an_error(tmp_path: Path) -> None:
    missing = tmp_path / "absent" / "config.toml"
    env = {"PLOTROOM_CONFIG": str(missing)}
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(None, env)


def test_no_file_at_the_default_location_leaves_environment_and_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(None, {"PLOTROOM_PROJECTS": "x"})
    assert config.projects == ("x",)
    assert config.port == 8765


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "no projects configured"),
        ("[projects.Bad_Case]\n", "lowercase slug"),
        ("[projects.'a b']\n", "lowercase slug"),
        ('port = "http"\n[projects.a]\n', "port must be an integer"),
        ("port = 70000\n[projects.a]\n", "between 0 and 65535"),
        ("hots = 'x'\n[projects.a]\n", "unknown config keys"),
        ("allowed_hosts = []\n[projects.a]\n", "must not be empty"),
        ("allowed_hosts = [1]\n[projects.a]\n", "list of strings"),
        ("projects = 3\n", "table of"),
        ("this is = not toml\n", "cannot read config"),
    ],
)
def test_bad_configuration_names_the_problem(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, text), {})


def test_a_relative_data_dir_from_the_environment_is_relative_to_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "conf"
    config_dir.mkdir()
    working = tmp_path / "work"
    working.mkdir()
    monkeypatch.chdir(working)
    path = write(config_dir, 'data_dir = "from-file"\n[projects.a]\n')
    assert load_config(path, {}).data_dir == config_dir / "from-file"
    env = {"PLOTROOM_DATA_DIR": "from-env"}
    assert load_config(path, env).data_dir == (working / "from-env").resolve()


@pytest.mark.parametrize("port", ["true", "1.5", '["a"]'])
def test_port_must_be_an_integer(tmp_path: Path, port: str) -> None:
    with pytest.raises(ConfigError, match="port must be an integer"):
        load_config(write(tmp_path, f"port = {port}\n[projects.a]\n"), {})


def test_a_wildcard_bind_is_reached_on_loopback(tmp_path: Path) -> None:
    v4 = load_config(write(tmp_path, 'host = "0.0.0.0"\nport = 9\n[projects.a]\n'), {})
    v6 = load_config(write(tmp_path, 'host = "::"\nport = 9\n[projects.a]\n'), {})
    assert v4.url == "http://127.0.0.1:9/mcp"
    assert v6.url == "http://[::1]:9/mcp"


def test_explicit_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path / "nope.toml", {"PLOTROOM_PROJECTS": "x"})


def test_projects_are_optional_for_a_client(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, "port = 9000\n"), {}, require_projects=False)
    assert config.projects == ()
    assert config.port == 9000


def test_drain_sessions_are_opt_in_and_project_scoped(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[projects.alpha]\ndrain_session = "alpha-desk"\n[projects.beta]\n',
    )
    assert load_config(path, {}).drain_sessions == {"alpha": "alpha-desk"}
    assert load_config(path, {"PLOTROOM_PROJECTS": "beta"}).drain_sessions == {}


@pytest.mark.parametrize("value", ['""', '"   "', "123", "true", "[]"])
def test_invalid_drain_sessions_are_rejected(tmp_path: Path, value: str) -> None:
    path = write(tmp_path, f"[projects.alpha]\ndrain_session = {value}\n")
    with pytest.raises(ConfigError, match=r"projects\.alpha\.drain_session"):
        load_config(path, {})


def test_duplicate_project_ids_from_the_environment(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unique"):
        load_config(write(tmp_path, ""), {"PLOTROOM_PROJECTS": "a,a"})


def test_a_config_file_that_is_not_utf8_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"data_dir = '\xff\xfe'\n")
    with pytest.raises(ConfigError, match="cannot read config"):
        load_config(path, {})


def test_log_rotation_defaults_are_ten_mebibytes_and_five_files(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, "[projects.a]\n"), {})
    assert config.log_max_bytes == DEFAULT_LOG_MAX_BYTES == 10 * 1024 * 1024
    assert config.log_backups == DEFAULT_LOG_BACKUPS == 5


def test_log_rotation_settings_come_from_the_file_and_the_environment_wins(tmp_path: Path) -> None:
    path = write(tmp_path, "log_max_bytes = 2048\nlog_backups = 0\n[projects.a]\n")
    config = load_config(path, {})
    assert (config.log_max_bytes, config.log_backups) == (2048, 0)
    env = {"PLOTROOM_LOG_MAX_BYTES": "4096", "PLOTROOM_LOG_BACKUPS": "7"}
    config = load_config(path, env)
    assert (config.log_max_bytes, config.log_backups) == (4096, 7)


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("log_max_bytes = 0", "log_max_bytes"),
        ("log_max_bytes = -5", "log_max_bytes"),
        ('log_max_bytes = "big"', "log_max_bytes"),
        ("log_max_bytes = 1.5", "log_max_bytes"),
        ("log_max_bytes = true", "log_max_bytes"),
        ("log_backups = -1", "log_backups"),
        ('log_backups = "many"', "log_backups"),
        ("log_backups = [1]", "log_backups"),
    ],
)
def test_a_bad_log_rotation_setting_names_its_key(tmp_path: Path, text: str, key: str) -> None:
    with pytest.raises(ConfigError, match=key):
        load_config(write(tmp_path, f"{text}\n[projects.a]\n"), {})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PLOTROOM_LOG_MAX_BYTES", "0"),
        ("PLOTROOM_LOG_MAX_BYTES", "-1"),
        ("PLOTROOM_LOG_MAX_BYTES", "10MB"),
        ("PLOTROOM_LOG_BACKUPS", "-1"),
        ("PLOTROOM_LOG_BACKUPS", "2.5"),
    ],
)
def test_a_bad_log_rotation_environment_value_is_a_config_error(
    tmp_path: Path, name: str, value: str
) -> None:
    key = name.removeprefix("PLOTROOM_").lower()
    with pytest.raises(ConfigError, match=key):
        load_config(write(tmp_path, "[projects.a]\n"), {name: value})
