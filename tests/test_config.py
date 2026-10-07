import pytest

from recon.config import (
    ConfigError, Aggregate, TestConfig, load_suite, resolve_env, save_suite, suite_from_dict,
)

SUITE = {
    "suite": "s",
    "description": "d",
    "tests": [
        {
            "name": "t1",
            "source": {"type": "sqlite", "url": "sqlite:///${DB_PATH:-x.db}", "query": "select 1"},
            "target": {"type": "csv", "path": "a.csv"},
            "keys": ["id"],
            "column_map": {"a": "b"},
            "tolerance": {"amount": 0.01},
            "aggregates": [{"column": "amount", "func": "sum", "tolerance": 0.05}],
            "thresholds": {"max_missing_pct": 1},
        }
    ],
}


def test_env_resolution():
    env = {"A": "1"}
    assert resolve_env("x${A}y", env) == "x1y"
    assert resolve_env("${B:-dflt}", env) == "dflt"
    assert resolve_env({"k": ["${A}", 3]}, env) == {"k": ["1", 3]}
    with pytest.raises(ConfigError):
        resolve_env("${MISSING}", env)


def test_defaults():
    s = suite_from_dict(SUITE)
    t = s.tests[0]
    assert t.options.trim_strings is True and t.options.case_insensitive is False
    assert t.options.max_mismatch_samples == 500
    assert t.thresholds.max_mismatch_pct == 0 and t.thresholds.allow_duplicate_keys is False
    assert t.enabled


def test_round_trip_keeps_placeholders(tmp_path):
    s = suite_from_dict(SUITE)
    p = tmp_path / "s.yaml"
    save_suite(s, p)
    assert "${DB_PATH:-x.db}" in p.read_text()  # unresolved on disk
    again = load_suite(p)
    assert again == s
    resolved = load_suite(p, resolve=True, env={"DB_PATH": "real.db"})
    assert resolved.tests[0].source["url"] == "sqlite:///real.db"


def test_invalid_aggregate():
    with pytest.raises(ConfigError):
        Aggregate(column="a", func="median")


def test_validation_errors():
    with pytest.raises(ConfigError):
        TestConfig(name="x", source={}, target={}, keys=[])
    with pytest.raises(ConfigError):
        suite_from_dict({"tests": []})
    with pytest.raises(ConfigError):
        suite_from_dict({"suite": "s", "tests": [{"name": "a"}]})
    dup = {"suite": "s", "tests": [SUITE["tests"][0], SUITE["tests"][0]]}
    with pytest.raises(ConfigError):
        suite_from_dict(dup)


def test_enabled_and_only():
    d = {"suite": "s", "tests": [dict(SUITE["tests"][0], name="a"), dict(SUITE["tests"][0], name="b", enabled=False)]}
    s = suite_from_dict(d)
    assert [t.name for t in s.enabled_tests()] == ["a"]
    assert [t.name for t in s.enabled_tests(["b"])] == ["b"]
    with pytest.raises(ConfigError):
        s.enabled_tests(["zzz"])
