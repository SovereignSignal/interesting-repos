import pytest
from datetime import date
from pathlib import Path
from bot.config import expand_since, load_themes, Theme, load_config, Config

def test_expand_since_replaces_token_with_iso_date():
    q = expand_since("created:>{since:7d}", today=date(2026, 5, 26))
    assert q == "created:>2026-05-19"

def test_expand_since_handles_multiple_and_other_windows():
    q = expand_since("a:{since:7d} b:{since:90d}", today=date(2026, 5, 26))
    assert q == "a:2026-05-19 b:2026-02-25"

def test_expand_since_leaves_plain_queries_untouched():
    assert expand_since("topic:finance", today=date(2026, 5, 26)) == "topic:finance"

SAMPLE = Path(__file__).parent / "data" / "themes_sample.toml"

def test_load_themes_parses_all_fields():
    themes = load_themes(str(SAMPLE))
    assert len(themes) == 2
    t = themes[0]
    assert (t.key, t.name, t.emoji, t.query, t.sort, t.count, t.rank) == (
        "top-stars", "Top Stars This Week", "🔥", "created:>{since:7d}", "stars", 5, "stars")

def test_load_themes_applies_defaults():
    fin = load_themes(str(SAMPLE))[1]
    assert fin.sort == "stars" and fin.order == "desc"
    assert fin.count == 7 and fin.rank == "stars" and fin.profile == ""

def _env(**overrides):
    base = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "-100123"}
    base.update(overrides)
    return base

def test_load_config_reads_required_and_optional():
    cfg = load_config(env=_env(GITHUB_TOKEN="gh", STATE_DIR="/tmp/x"), themes_path=str(SAMPLE))
    assert isinstance(cfg, Config)
    assert cfg.telegram_bot_token == "tok"
    assert cfg.telegram_chat_id == "-100123"
    assert cfg.github_token == "gh"
    assert cfg.state_dir == "/tmp/x"
    assert len(cfg.themes) == 2

def test_load_config_defaults_state_dir_and_blank_optionals():
    cfg = load_config(env=_env(), themes_path=str(SAMPLE))
    assert cfg.state_dir == "/data"
    assert cfg.github_token == ""
    assert cfg.ollama_host == "https://ollama.com" and cfg.ollama_model == "gemma3:12b"
    assert cfg.ollama_api_key == ""

def test_load_config_missing_required_var_exits():
    with pytest.raises(SystemExit):
        load_config(env={"TELEGRAM_CHAT_ID": "-1"}, themes_path=str(SAMPLE))
