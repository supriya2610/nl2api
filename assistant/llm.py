import os


def anthropic_complete(system: str, user: str) -> str:
    import anthropic
    r = anthropic.Anthropic().messages.create(
        model=os.getenv("PLANNER_MODEL", "claude-haiku-5-5"), max_tokens=1500,
        system=system, messages=[{"role": "user", "content": user}])
    return r.content[0].text
