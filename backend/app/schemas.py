from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ParseStatus = Literal["pending", "parsing", "parsed", "failed"]
TrialStatus = Literal["waiting", "evidence", "voting", "closed"]
Side = Literal["radiant", "dire"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PlayerCreate(BaseModel):
    steam_id: int = Field(gt=0)
    display_name: str = Field(min_length=1, max_length=100)

    @field_validator("steam_id")
    @classmethod
    def normalize_steam_id(cls, value: int) -> int:
        steam_id64_offset = 76561197960265728
        if value >= steam_id64_offset:
            value -= steam_id64_offset
        if value > 0xFFFFFFFF:
            raise ValueError("steam_id must be a 32-bit account_id or valid SteamID64")
        return value


class PlayerRead(ORMModel):
    id: int
    steam_id: int
    display_name: str
    avatar_url: str | None
    is_active: bool
    created_at: datetime


class MatchPlayerRead(ORMModel):
    id: int
    match_id: int
    player_id: int | None
    hero_id: int
    hero_name: str | None
    lane_role: str | None
    is_our_team: bool
    kills: int | None
    deaths: int | None
    assists: int | None
    gpm: int | None
    xpm: int | None
    net_worth: int | None
    lh_at_10: int | None
    damage_share: float | None
    teamfight_participation: float | None
    obs_placed: int | None
    sen_placed: int | None
    tp_uses: int | None
    buybacks: int | None
    stuns: float | None
    tower_damage: int | None
    metrics_json: str | None


class MatchRead(ORMModel):
    id: int
    match_id: int
    started_at: datetime | None
    duration: int | None
    radiant_win: bool | None
    our_side: Side | None
    we_won: bool | None
    parse_status: ParseStatus
    raw_json: str | None
    evidence_json: str | None
    nominees_json: str | None
    created_at: datetime


class MatchListItem(ORMModel):
    """案卷库列表项。刻意不含 raw_json —— 那是几百 KB 的 OpenDota 原始包，
    列表里带上会让移动端微信直接卡死。详情用 GET /api/matches/{match_id}。"""

    id: int
    match_id: int
    started_at: datetime | None
    duration: int | None
    radiant_win: bool | None
    our_side: Side | None
    we_won: bool | None
    parse_status: ParseStatus
    created_at: datetime


class TrialRead(ORMModel):
    id: int
    match_id: int
    status: TrialStatus
    vote_started_at: datetime | None
    vote_deadline: datetime | None
    verdict_player_id: int | None
    verdict_json: str | None
    appeal_text: str | None
    ai_verdict_player_id: int | None
    ai_verdict_json: str | None
    created_at: datetime
    closed_at: datetime | None


class AttendanceRead(ORMModel):
    id: int
    trial_id: int
    player_id: int
    arrived_at: datetime


class VoteCreate(BaseModel):
    nominee_id: int = Field(gt=0)


class VoteRead(ORMModel):
    id: int
    trial_id: int
    voter_id: int
    nominee_id: int
    created_at: datetime


class AppealCreate(BaseModel):
    text: str = Field(min_length=1, max_length=60)
