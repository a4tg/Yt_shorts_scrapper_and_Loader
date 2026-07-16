import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebMessagesTests(unittest.TestCase):
    def test_messages_workspace_and_composer_are_present(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('/assets/messages.css', html)
        self.assertIn('data-navigate="messages"', html)
        self.assertIn('data-page="messages"', html)
        for element_id in (
            "conversation-list",
            "new-conversation-button",
            "message-list",
            "message-composer",
            "message-attachment",
            "chat-details",
            "content-discussion-button",
        ):
            self.assertIn(f'id="{element_id}"', html)

    def test_messages_client_supports_groups_direct_context_and_unread(self) -> None:
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("loadMessagingWorkspace()", script)
        self.assertIn("/conversations`", script)
        self.assertIn("/messages`", script)
        self.assertIn("/conversation`, { method: 'POST' }", script)
        self.assertIn("updateMessagesNavBadge()", script)
        self.assertIn("startMessagePolling()", script)

    def test_empty_success_responses_are_not_parsed_as_json(self) -> None:
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("if (response.status === 204) return null;", script)
        self.assertIn("return body ? JSON.parse(body) : null;", script)

    def test_messages_styles_include_responsive_three_panel_layout(self) -> None:
        styles = (ROOT / "web" / "messages.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns:290px minmax(420px,1fr) 250px", styles)
        self.assertIn(".message-bubble-row.own", styles)
        self.assertIn("@media(max-width:760px)", styles)

    def test_chat_anywhere_module_has_persistent_floating_realtime_ux(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        entrypoint = (ROOT / "web" / "workspace-depth.js").read_text(encoding="utf-8")
        script = (ROOT / "web" / "modules" / "chat-anywhere.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "chat-anywhere.css").read_text(encoding="utf-8")
        self.assertIn('/assets/chat-anywhere.css', html)
        self.assertIn("registerModule('chat-anywhere'", entrypoint)
        self.assertIn("new EventSource", script)
        self.assertIn("aapChatAnywhereLayoutV1", script)
        self.assertIn("mentioned_user_ids", script)
        self.assertIn("/pinned-messages", script)
        self.assertIn("data-mode=docked", styles)
        self.assertIn("resize:both", styles)


if __name__ == "__main__":
    unittest.main()
