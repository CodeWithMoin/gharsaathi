"""Per-session instructions built from the user's own saved profile."""

from app.user_profile import UserProfile


LANGUAGE_LABELS = {"hinglish": "Hinglish", "hindi": "Hindi", "english": "English"}

# The saved language overrides the default Hinglish rules in the prompts; the
# user can still ask for another language during the session.
LANGUAGE_RULES = {
    "hinglish": (
        "Reply in Hinglish written in Roman script, as the language rules above describe."
    ),
    "english": (
        "Reply in plain English. This choice overrides the default Hinglish rules above: "
        "use no Hindi or Hinglish words or Devanagari (say \"Okay\", not \"Theek hai\"; "
        "\"rupees\", not \"rupaye\"), unless the user asks for another language in this session."
    ),
    "hindi": (
        "Reply in Hindi written in Devanagari script, keeping only brand names, product "
        "names and pack sizes in English. This choice overrides the default Roman-script "
        "Hinglish rules above, unless the user asks for another language in this session."
    ),
}


def session_profile_block(profile: UserProfile | None) -> str:
    """Instructions appended for one session; empty when the user saved nothing."""
    if profile is None:
        return ""
    lines = [
        "",
        "# Session user profile (chosen by the user in settings)",
        f"- Address form for the user: {profile.address_form}",
        f"- Preferred reply language: {LANGUAGE_LABELS[profile.language]}",
        f"- Reply language rule: {LANGUAGE_RULES[profile.language]}",
    ]
    if profile.first_name:
        lines.append(f"- Preferred first name: {profile.first_name}")
    return "\n".join(lines) + "\n"


def with_profile(instructions: str, profile: UserProfile | None) -> str:
    return instructions + session_profile_block(profile)
