from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    steam_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration: Mapped[int | None] = mapped_column(Integer)
    radiant_win: Mapped[bool | None] = mapped_column(Boolean)
    our_side: Mapped[str | None] = mapped_column(String(10))
    we_won: Mapped[bool | None] = mapped_column(Boolean)
    parse_status: Mapped[str] = mapped_column(String(20), default="pending")
    raw_json: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[str | None] = mapped_column(Text)
    nominees_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    players: Mapped[list["MatchPlayer"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )
    trial: Mapped["Trial | None"] = relationship(back_populates="match")


class MatchPlayer(Base):
    __tablename__ = "match_players"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    hero_id: Mapped[int] = mapped_column(Integer)
    hero_name: Mapped[str | None] = mapped_column(String(100))
    lane_role: Mapped[str | None] = mapped_column(String(50))
    is_our_team: Mapped[bool] = mapped_column(Boolean)
    kills: Mapped[int | None] = mapped_column(Integer)
    deaths: Mapped[int | None] = mapped_column(Integer)
    assists: Mapped[int | None] = mapped_column(Integer)
    gpm: Mapped[int | None] = mapped_column(Integer)
    xpm: Mapped[int | None] = mapped_column(Integer)
    net_worth: Mapped[int | None] = mapped_column(Integer)
    lh_at_10: Mapped[int | None] = mapped_column(Integer)
    damage_share: Mapped[float | None] = mapped_column(Float)
    teamfight_participation: Mapped[float | None] = mapped_column(Float)
    obs_placed: Mapped[int | None] = mapped_column(Integer)
    sen_placed: Mapped[int | None] = mapped_column(Integer)
    tp_uses: Mapped[int | None] = mapped_column(Integer)
    buybacks: Mapped[int | None] = mapped_column(Integer)
    stuns: Mapped[float | None] = mapped_column(Float)
    tower_damage: Mapped[int | None] = mapped_column(Integer)
    metrics_json: Mapped[str | None] = mapped_column(Text)

    match: Mapped[Match] = relationship(back_populates="players")
    player: Mapped[Player | None] = relationship()


class Trial(Base):
    __tablename__ = "trials"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="waiting")
    vote_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vote_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verdict_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    verdict_json: Mapped[str | None] = mapped_column(Text)
    appeal_text: Mapped[str | None] = mapped_column(String(60))
    ai_verdict_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    ai_verdict_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    match: Mapped[Match] = relationship(back_populates="trial")
    attendances: Mapped[list["Attendance"]] = relationship(
        back_populates="trial", cascade="all, delete-orphan"
    )
    votes: Mapped[list["Vote"]] = relationship(
        back_populates="trial", cascade="all, delete-orphan"
    )


class Attendance(Base):
    __tablename__ = "attendances"
    __table_args__ = (UniqueConstraint("trial_id", "player_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    trial_id: Mapped[int] = mapped_column(ForeignKey("trials.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    arrived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    trial: Mapped[Trial] = relationship(back_populates="attendances")
    player: Mapped[Player] = relationship()


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("trial_id", "voter_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    trial_id: Mapped[int] = mapped_column(ForeignKey("trials.id"))
    voter_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    nominee_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    trial: Mapped[Trial] = relationship(back_populates="votes")
    voter: Mapped[Player] = relationship(foreign_keys=[voter_id])
    nominee: Mapped[Player] = relationship(foreign_keys=[nominee_id])
