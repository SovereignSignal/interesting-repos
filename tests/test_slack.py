import httpx

from bot.slack import html_to_mrkdwn, send_slack_message


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_html_to_mrkdwn_header_bold_and_clean_link():
    # header <b> (outside a link) → *bold*; heading <a><b>title</b></a> → clean <url|title>
    html = ('🔥 <b>Top Stars</b>\n\n'
            '<a href="https://x/1"><b>Cool Tool</b></a>\n'
            '⭐ 1,500 · Rust\nUses &lt;async&gt; &amp; speed')
    md = html_to_mrkdwn(html)
    assert "*Top Stars*" in md                       # header bold
    assert "<https://x/1|Cool Tool>" in md           # clean Slack link
    assert "**<" not in md and "*<https" not in md   # NOT a broken bold-before-link
    assert "&lt;async&gt; &amp; speed" in md         # entities re-escaped for Slack
    assert "<a href" not in md and "</b>" not in md


def test_send_slack_message_dormant_without_creds():
    calls = []
    client = _client(lambda r: calls.append(r) or httpx.Response(200, json={"ok": True}))
    assert send_slack_message("", "C1", "<b>hi</b>", client=client) is False
    assert send_slack_message("xoxb", "", "<b>hi</b>", client=client) is False
    assert calls == []                              # no POST when unconfigured


def test_send_slack_message_posts_converted_text():
    captured = {}
    def handler(request):
        import json as _json
        captured["url"] = str(request.url)
        captured["body"] = _json.loads(request.content)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})
    ok = send_slack_message("xoxb-test", "C0B5", "<b>ModelBytes</b>", client=_client(handler))
    assert ok is True
    assert "chat.postMessage" in captured["url"]
    assert captured["body"]["channel"] == "C0B5"
    assert "*ModelBytes*" in captured["body"]["text"]
    assert captured["auth"] == "Bearer xoxb-test"


def test_send_slack_message_false_on_api_error():
    client = _client(lambda r: httpx.Response(200, json={"ok": False, "error": "channel_not_found"}))
    assert send_slack_message("xoxb", "C1", "<b>hi</b>", client=client) is False


def test_send_slack_message_false_on_transport_error():
    def boom(request):
        raise httpx.ConnectError("down")
    assert send_slack_message("xoxb", "C1", "<b>hi</b>", client=_client(boom)) is False
