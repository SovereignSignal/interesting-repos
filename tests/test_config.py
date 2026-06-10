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


def test_load_themes_defaults_catch_all_and_idle(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\n')
    t = load_themes(str(p))[0]
    assert t.catch_all is False and t.max_idle_days == 60


def test_load_themes_reads_catch_all_and_idle(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\ncatch_all=true\nmax_idle_days=30\n')
    t = load_themes(str(p))[0]
    assert t.catch_all is True and t.max_idle_days == 30


def test_load_themes_reads_agent_skill_cap(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nagent_skill_cap=2\n')
    assert load_themes(str(p))[0].agent_skill_cap == 2


def test_load_themes_agent_skill_cap_absent_is_none(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\n')
    assert load_themes(str(p))[0].agent_skill_cap is None


def test_load_themes_agent_skill_cap_zero_is_kept(tmp_path):
    # 0 must survive as 0 (exclude policy), not be coerced to None
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nagent_skill_cap=0\n')
    assert load_themes(str(p))[0].agent_skill_cap == 0


def test_load_config_reads_send_delay_seconds():
    cfg = load_config(env=_env(SEND_DELAY_SECONDS="7"), themes_path=str(SAMPLE))
    assert cfg.send_delay_seconds == 7.0


def test_load_config_send_delay_defaults_to_throttle():
    # default is a real pause so a 10-message digest trickles instead of flooding
    assert load_config(env=_env(), themes_path=str(SAMPLE)).send_delay_seconds == 20.0



def test_load_config_reads_slack_creds():
    cfg = load_config(env=_env(SLACK_BOT_TOKEN="xoxb-1", SLACK_CHANNEL_ID="C123"),
                      themes_path=str(SAMPLE))
    assert cfg.slack_bot_token == "xoxb-1" and cfg.slack_channel_id == "C123"


def test_load_config_slack_creds_default_blank():
    cfg = load_config(env=_env(), themes_path=str(SAMPLE))
    assert cfg.slack_bot_token == "" and cfg.slack_channel_id == ""


def test_load_config_reads_alert_chat_id():
    cfg = load_config(env=_env(ALERT_CHAT_ID="123456789"), themes_path=str(SAMPLE))
    assert cfg.alert_chat_id == "123456789"


def test_load_config_alert_chat_id_default_blank():
    assert load_config(env=_env(), themes_path=str(SAMPLE)).alert_chat_id == ""


def test_load_themes_parses_at_to_weekday_hour_pairs(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nat=["mon 13", "thu 16"]\n')
    assert load_themes(str(p))[0].at == ((0, 13), (3, 16))   # (weekday, UTC hour)


def test_load_themes_at_absent_is_none(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\n')
    assert load_themes(str(p))[0].at is None


def test_load_themes_at_invalid_day_exits(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nat=["funday 13"]\n')
    with pytest.raises(SystemExit):
        load_themes(str(p))


def test_load_themes_at_invalid_hour_exits(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nat=["mon 24"]\n')
    with pytest.raises(SystemExit):
        load_themes(str(p))


def test_load_themes_at_malformed_entry_exits(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[[theme]]\nkey="k"\nname="N"\nquery="q"\nat=["mon"]\n')
    with pytest.raises(SystemExit):
        load_themes(str(p))
