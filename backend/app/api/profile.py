from fastapi import APIRouter

from app.auth import UserIdDependency
from app.memory.dependencies import DbSessionDependency
from app.user_profile import (
    UserProfile,
    UserProfileUpdate,
    get_profile_record,
    profile_for,
    update_profile,
)


router = APIRouter()


@router.get("", response_model=UserProfile)
async def get_profile(session: DbSessionDependency, user_id: UserIdDependency) -> UserProfile:
    return profile_for(await get_profile_record(session, user_id))


@router.put("", response_model=UserProfile)
async def put_profile(
    body: UserProfileUpdate, session: DbSessionDependency, user_id: UserIdDependency
) -> UserProfile:
    return profile_for(await update_profile(session, user_id, body))
