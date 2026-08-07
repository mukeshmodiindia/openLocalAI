"""Shared error type raised when an integration is disabled or missing from
config.yaml. Lets you deploy the agent with only the LLM running, then
enable ServiceNow/Confluence/DB/Slack later just by editing config.yaml and
either restarting the agent container or calling POST /admin/reload-config
— no redeploy needed.
"""


class IntegrationNotConfigured(RuntimeError):
    def __init__(self, name: str, hint: str = ""):
        msg = f"{name} is not configured yet."
        if hint:
            msg += f" {hint}"
        else:
            msg += (
                f" Set {name.lower()}.enabled: true and fill in its "
                "connection details in config.yaml, then restart the agent "
                "or POST /admin/reload-config."
            )
        super().__init__(msg)
        self.integration = name
