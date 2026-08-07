"""Slack integration via Bolt SDK, socket mode (no public webhook/ingress
needed — works from inside your network). Used to post generated plans for
human approval and to receive follow-up instructions."""
from __future__ import annotations

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.config import get_config
from src.connectors.errors import IntegrationNotConfigured


class SlackConnector:
    def __init__(self, on_message=None):
        slack = get_config().raw.get("slack", {})
        self.enabled = slack.get("enabled", False)
        if not self.enabled:
            raise IntegrationNotConfigured("Slack")

        self.default_channel = slack.get("default_channel", "#general")
        self.post_for_approval = slack.get("post_plan_for_approval", True)

        self.app = App(token=slack["bot_token"])
        self._app_token = slack["app_token"]

        if on_message:
            self.app.event("message")(on_message)

    def post_plan_for_review(self, plan_text: str, channel: str | None = None):
        self.app.client.chat_postMessage(
            channel=channel or self.default_channel,
            text=plan_text,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": plan_text}},
                {
                    "type": "actions",
                    "elements": [
                        {"type": "button", "text": {"type": "plain_text", "text": "Approve"},
                         "style": "primary", "value": "approve", "action_id": "approve_plan"},
                        {"type": "button", "text": {"type": "plain_text", "text": "Reject"},
                         "style": "danger", "value": "reject", "action_id": "reject_plan"},
                    ],
                },
            ],
        )

    def start(self):
        """Blocking call — run in its own process/thread."""
        SocketModeHandler(self.app, self._app_token).start()
