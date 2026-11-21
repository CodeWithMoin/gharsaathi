"""Per-session instructions built from the user's own saved profile."""

from app.user_profile import UserProfile


LANGUAGE_LABELS = {"hinglish": "Hinglish", "hindi": "Hindi", "english": "English"}


def session_profile_block(profile: UserProfile | None) -> str:
    """Instructions appended for one session; empty when the user saved nothing."""
    if profile is None:
        return ""
    lines = [
        "",
        "# Session user profile (chosen by the user in settings)",
        f"- Address form for the user: {profile.address_form}",
        f"- Preferred reply language: {LANGUAGE_LABELS[profile.language]}",
    ]
    if profile.first_name:
        lines.append(f"- Preferred first name: {profile.first_name}")
    return "\n".join(lines) + "\n"


def with_profile(instructions: str, profile: UserProfile | None) -> str:
    return instructions + session_profile_block(profile)
