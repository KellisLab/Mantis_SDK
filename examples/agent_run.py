"""run an agent (opencode by default, claude_code opt-in) scoped to a space, streaming output.

auth: a browser session cookie in MANTIS_COOKIE. the agent runtime identifies the user by
EMAIL (MANTIS_USER_EMAIL), not user_id. claude_code additionally requires that user to have
bedrock_enabled in their capabilities — the SDK checks this up front and raises
ProviderUnavailableError instead of silently downgrading."""
import asyncio
import os

from mantis_sdk import ConfigurationManager, MantisClient, Provider
from mantis_sdk.exceptions import ProviderUnavailableError

config = ConfigurationManager().update({
    "host": os.getenv("MANTIS_HOST", "http://localhost:3000"),
    "backend_host": os.getenv("MANTIS_BACKEND_HOST", "http://localhost:8000"),
    "user_email": os.environ["MANTIS_USER_EMAIL"],
})
client = MantisClient("/api/proxy/", cookie=os.environ["MANTIS_COOKIE"], config=config)

SPACE_ID = os.getenv("MANTIS_SPACE_ID")  # optional: scope the agent to a space/map
PROVIDER = Provider(os.getenv("MANTIS_PROVIDER", "opencode"))  # opencode is the safe default


async def main():
    # which providers can this user actually use?
    print("providers:", client.agents.providers(config.user_email))

    try:
        async with client.agents.session(SPACE_ID, provider=PROVIDER) as run:
            print(f"[running with {PROVIDER}]")
            async for ev in run.ask("Give me a one-sentence summary of this space."):
                if ev.type == "text":
                    print(ev.text, end="", flush=True)
                elif ev.type == "tool_use":
                    print(f"\n  → tool: {ev.tool_name}", flush=True)
                elif ev.type == "fail":
                    print(f"\n[run failed] {ev.text}", flush=True)
            print()
            result = run.result()
            print(f"[done] provider={result.provider} failed={result.failed}")
    except ProviderUnavailableError as exc:
        # e.g. claude_code requested but the user isn't bedrock_enabled.
        print(f"[provider unavailable] {exc}")


asyncio.run(main())
