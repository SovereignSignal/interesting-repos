from pathlib import Path
from types import SimpleNamespace

from bot import formatter

ROOT = Path(__file__).resolve().parent.parent
FOOTER = "Curated by Raven, Sov's Grok Bot"
SENTENCE = "You are Raven, Sov's Grok Bot. Write in your own voice. Never write as or for Sov."


def test_footer_on_last_message_only(monkeypatch):
    calls = iter(["A" * 3000, "B" * 3000])
    monkeypatch.setattr(formatter, "_entry", lambda *a, **k: next(calls))
    theme = SimpleNamespace(emoji="🔥", name="Hot")
    msgs = formatter.build_messages(theme, [object(), object()], describe=None, titles=["a", "b"])
    assert len(msgs) == 2
    assert FOOTER not in msgs[0]
    assert msgs[-1].endswith(FOOTER)


def test_prompts_carry_sentence():
    for rel in ("bot/summaries.py", "bot/ranker.py"):
        assert SENTENCE in (ROOT / rel).read_text(), rel
