from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

BigInt = BigInteger().with_variant(Integer, "sqlite")


def ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    is_blocked_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts()


class Tournament(Base):
    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    banner_url: Mapped[str | None] = mapped_column(String(500))
    format: Mapped[str] = mapped_column(String(10))  # 1v1/2v2/3v3/5v5
    stage_type: Mapped[str] = mapped_column(String(20), default="single_elim")
    max_participants: Mapped[int] = mapped_column(Integer, default=16)
    reg_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reg_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prizes: Mapped[str] = mapped_column(Text, default="")
    rules: Mapped[str] = mapped_column(Text, default="")
    pairing_method: Mapped[str] = mapped_column(String(10), default="random")
    result_policy: Mapped[str] = mapped_column(
        String(30), default="captain_then_admin"
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status in ('draft','reg_open','reg_closed','paired','running',"
            "'finished','cancelled')",
            name="ck_tournament_status",
        ),
        CheckConstraint("max_participants > 0", name="ck_tournament_max"),
    )


class RequiredChannel(Base):
    __tablename__ = "required_channels"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    chat_ref: Mapped[str] = mapped_column(String(100), unique=True)  # @user yoki -100..
    title: Mapped[str] = mapped_column(String(150))
    invite_url: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = ts()


class Registration(Base):
    """O'yinchining turnirga ro'yxatdan o'tishi + chipta."""

    __tablename__ = "registrations"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    tournament_id: Mapped[int] = mapped_column(
        ForeignKey("tournaments.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    seq_no: Mapped[int] = mapped_column(Integer)  # turnir ichida takrorlanmas
    ticket_code: Mapped[str] = mapped_column(String(24), unique=True)
    status: Mapped[str] = mapped_column(String(15), default="registered")
    created_at: Mapped[datetime] = ts()

    __table_args__ = (
        UniqueConstraint("tournament_id", "user_id", name="uq_reg_tournament_user"),
        UniqueConstraint("tournament_id", "seq_no", name="uq_reg_tournament_seq"),
        CheckConstraint(
            "status in ('registered','waitlist','cancelled')", name="ck_reg_status"
        ),
    )


class PlayerProfile(Base):
    """MLBB ma'lumotlari (bir marta kiritiladi)."""

    __tablename__ = "player_profiles"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    mlbb_nick: Mapped[str] = mapped_column(String(64))
    mlbb_id: Mapped[str] = mapped_column(String(20))
    server_id: Mapped[str] = mapped_column(String(10))
    contact: Mapped[str | None] = mapped_column(String(100))
    global_seq: Mapped[int] = mapped_column(Integer, unique=True)
    created_at: Mapped[datetime] = ts()


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    tournament_id: Mapped[int] = mapped_column(
        ForeignKey("tournaments.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(40))
    captain_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(15), default="forming")
    created_at: Mapped[datetime] = ts()

    members: Mapped[list["TeamMember"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tournament_id", "name", name="uq_team_name"),
        CheckConstraint(
            "status in ('forming','full','disbanded')", name="ck_team_status"
        ),
    )


class TeamMember(Base):
    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    tournament_id: Mapped[int] = mapped_column(
        ForeignKey("tournaments.id", ondelete="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    joined_at: Mapped[datetime] = ts()

    team: Mapped[Team] = relationship(back_populates="members")

    __table_args__ = (
        # bir o'yinchi bir turnirda faqat bitta jamoada
        UniqueConstraint("tournament_id", "user_id", name="uq_member_tournament_user"),
    )


class TeamInvitation(Base):
    __tablename__ = "team_invitations"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    invitee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = ts()
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status in ('pending','accepted','declined','cancelled','expired')",
            name="ck_invite_status",
        ),
        # bir jamoadan bir o'yinchiga bitta kutilayotgan taklif
        Index(
            "uq_invite_pending",
            "team_id",
            "invitee_id",
            unique=True,
            postgresql_where="status = 'pending'",
            sqlite_where="status = 'pending'",
        ),
    )


class Round(Base):
    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    tournament_id: Mapped[int] = mapped_column(
        ForeignKey("tournaments.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(50))
    group_label: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12), default="pending")

    __table_args__ = (
        UniqueConstraint(
            "tournament_id", "number", "group_label", name="uq_round_number"
        ),
    )


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    tournament_id: Mapped[int] = mapped_column(
        ForeignKey("tournaments.id", ondelete="CASCADE"), index=True
    )
    round_id: Mapped[int] = mapped_column(
        ForeignKey("rounds.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    team1_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    team2_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    is_bye: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(15), default="scheduled")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    next_match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id"))
    next_slot: Mapped[int | None] = mapped_column(Integer)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)  # juftlik tasdiqlandi
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint(
            "team1_id is null or team2_id is null or team1_id <> team2_id",
            name="ck_match_distinct_teams",
        ),
        CheckConstraint(
            "status in ('scheduled','awaiting','finished','cancelled')",
            name="ck_match_status",
        ),
        UniqueConstraint("round_id", "position", name="uq_match_round_pos"),
    )


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), index=True
    )
    submitted_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    team1_score: Mapped[int] = mapped_column(Integer)
    team2_score: Mapped[int] = mapped_column(Integer)
    screenshot_file_id: Mapped[str | None] = mapped_column(String(300))
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = ts()

    __table_args__ = (
        CheckConstraint(
            "status in ('pending','approved','rejected')", name="ck_result_status"
        ),
        CheckConstraint("team1_score >= 0 and team2_score >= 0", name="ck_score_pos"),
        # bir o'yinda faqat bitta tasdiqlangan natija
        Index(
            "uq_result_approved",
            "match_id",
            unique=True,
            postgresql_where="status = 'approved'",
            sqlite_where="status = 'approved'",
        ),
    )


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    tournament_id: Mapped[int | None] = mapped_column(
        ForeignKey("tournaments.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="draft")
    created_by: Mapped[int] = mapped_column(BigInteger)
    sent_ok: Mapped[int] = mapped_column(Integer, default=0)
    sent_fail: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = ts()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status in ('draft','sending','sent','failed')", name="ck_ann_status"
        ),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(60))
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str | None] = mapped_column(String(40))
    details: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[str] = mapped_column(String(10), default="ok")
    created_at: Mapped[datetime] = ts()


class ProcessedUpdate(Base):
    """Takroriy Telegram yangilanishlaridan himoya."""

    __tablename__ = "processed_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = ts()


class IdempotencyKey(Base):
    """Takroriy API so'rovlaridan himoya."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = ts()


class AppConfig(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
