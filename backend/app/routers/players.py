from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Player
from app.opendota import OpenDotaClient
from app.schemas import PlayerCreate, PlayerOption, PlayerRead

router = APIRouter(prefix="/api/players", tags=["players"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[PlayerOption])
async def list_players(session: Session) -> list[Player]:
    result = await session.scalars(
        select(Player)
        .where(Player.is_active.is_(True))
        .order_by(Player.created_at, Player.id)
    )
    return list(result)


@router.post("", response_model=PlayerRead, status_code=status.HTTP_201_CREATED)
async def create_player(
    payload: PlayerCreate, session: Session
) -> Player:
    player = Player(steam_id=payload.steam_id, display_name=payload.display_name)
    session.add(player)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="steam_id already exists") from exc
    await session.refresh(player)
    return player


@router.delete("/{player_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_player(
    player_id: int, session: Session
) -> Response:
    player = await session.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="player not found")
    player.is_active = False
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/resolve/{steam_id}")
async def resolve_player(steam_id: int) -> dict[str, Any]:
    normalized = PlayerCreate(steam_id=steam_id, display_name="resolve").steam_id
    async with OpenDotaClient() as client:
        profile = await client.get_player(normalized)
    if not profile or not profile.get("profile"):
        raise HTTPException(status_code=502, detail="OpenDota player lookup failed")
    data = profile["profile"]
    return {
        "steam_id": normalized,
        "display_name": data.get("personaname"),
        "avatar_url": data.get("avatarfull") or data.get("avatar"),
    }