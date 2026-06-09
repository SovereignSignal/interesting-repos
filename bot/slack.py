import re
from html.parser import HTMLParser

import httpx


class _SlackMrkdwnConverter(HTMLParser):
    """Convert the small Telegram-HTML subset we emit (<b>, <i>, <a href>, <code>)
    into Slack mrkdwn: *bold*, _italic_, <url|label>, preserving line breaks.
    Ported from modelbytes so Slack rendering matches the other bots."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._in_link = False
        self._href = ""
        self._link_text: list[str] = []

    @staticmethod
    def _esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._in_link = True
            self._href = dict(attrs).get("href", "") or ""
            self._link_text = []
        elif self._in_link:
            return  # inside a link the label is plain text — ignore nested formatting
        elif tag in ("b", "strong"):
            self.parts.append("*")
        elif tag in ("i", "em"):
            self.parts.append("_")
        elif tag in ("code", "pre"):
            self.parts.append("`")
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "a":
            label = "".join(self._link_text).strip()
            href = self._href.strip()
            if href and label:
                self.parts.append(f"<{href}|{self._esc(label).replace('|', '/')}>")
            elif href:
                self.parts.append(f"<{href}>")
            else:
                self.parts.append(self._esc(label))
            self._in_link = False
            self._href = ""
            self._link_text = []
        elif self._in_link:
            return
        elif tag in ("b", "strong"):
            self.parts.append("*")
        elif tag in ("i", "em"):
            self.parts.append("_")
        elif tag in ("code", "pre"):
            self.parts.append("`")

    def handle_data(self, data):
        if self._in_link:
            self._link_text.append(data)
        else:
            self.parts.append(self._esc(data))

    def result(self) -> str:
        return "".join(self.parts)


def html_to_mrkdwn(value: str) -> str:
    """Telegram-HTML → Slack mrkdwn (bold/italic/links), blank lines collapsed."""
    parser = _SlackMrkdwnConverter()
    parser.feed(value)
    text = parser.result()
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def send_slack_message(token: str, channel: str, html: str,
                       client: httpx.Client | None = None) -> bool:
    """Mirror a digest message to Slack via chat.postMessage.

    No-op (returns False) when token/channel is unset, so a Telegram-only deploy is
    unaffected. Returns False on any error or when ok != true; never raises — Telegram
    is the primary channel.
    """
    if not token or not channel:
        return False
    text = html_to_mrkdwn(html)
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        resp = client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json; charset=utf-8"},
            json={"channel": channel, "text": text[:39000],
                  "unfurl_links": False, "unfurl_media": False},
        )
        return bool(resp.json().get("ok"))
    except Exception:
        return False
    finally:
        if owns_client:
            client.close()
