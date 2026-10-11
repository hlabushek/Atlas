import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, JSON, Date, DateTime, Float, ForeignKey, UniqueConstraint, Integer, Boolean, LargeBinary
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def uid(): return str(uuid.uuid4())
def now(): return datetime.now(timezone.utc)

class Base(DeclarativeBase): pass

class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(1000), default='')
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Message(Base):
    attachments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    attachment_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    initiative_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)

class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    category: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(100), default="owner")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    access: Mapped[str] = mapped_column(String(30), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Task(Base):
    attachments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    model_profile: Mapped[dict | None]=mapped_column(JSON,nullable=True)
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("conversation_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="chat")
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    max_steps: Mapped[int] = mapped_column(Integer, default=8)
    request_key: Mapped[str] = mapped_column(String(36))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)
    plan: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class AnswerFragment(Base):
    __tablename__ = 'answer_fragments'
    __table_args__ = (UniqueConstraint('task_id', 'claim', 'sequence'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id', ondelete='CASCADE'), index=True)
    claim: Mapped[str] = mapped_column(String(36))
    sequence: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class ToolEvent(Base):
    __tablename__ = 'tool_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id', ondelete='CASCADE'), index=True)
    call_id: Mapped[str] = mapped_column(String(36))
    claim: Mapped[str] = mapped_column(String(36))
    type: Mapped[str] = mapped_column(String(30))
    tool: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    step: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event: Mapped[str] = mapped_column(String(60))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (UniqueConstraint("task_id", "step"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    step: Mapped[int] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(String(100))
    arguments: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="PREPARED")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    call_id: Mapped[str] = mapped_column(ForeignKey("tool_calls.id"), unique=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    reason: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class ToolGrant(Base):
    __tablename__ = "tool_grants"
    tool: Mapped[str] = mapped_column(String(100), primary_key=True)
    allowed: Mapped[bool] = mapped_column(Boolean)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scope_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

class Note(Base):
    __tablename__ = "notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    call_id: Mapped[str] = mapped_column(ForeignKey("tool_calls.id"), unique=True)
    title: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class NotificationSettings(Base):
    __tablename__="notification_settings"
    id=mapped_column(Integer,primary_key=True)
    enabled=mapped_column(Boolean,default=False)
    enabled_at=mapped_column(DateTime(timezone=True),nullable=True)
    context_enabled=mapped_column(Boolean,default=False,nullable=False)
    timezone=mapped_column(String(80),default="Europe/Moscow")
    quiet_start=mapped_column(Integer,default=22)
    quiet_end=mapped_column(Integer,default=8)
    daily_limit=mapped_column(Integer,default=3)
    max_repeats=mapped_column(Integer,default=1)
    repeat_hours=mapped_column(Integer,default=4)
    channel=mapped_column(String(20),default="web")

class Initiative(Base):
    __tablename__="initiatives"
    id=mapped_column(String(36),primary_key=True,default=uid)
    event_key=mapped_column(String(150),unique=True)
    reason=mapped_column(String(300))
    message=mapped_column(Text)
    priority=mapped_column(Integer,default=1)
    related=mapped_column(JSON,default=dict)
    conversation_id=mapped_column(ForeignKey("conversations.id"),nullable=True)
    channel=mapped_column(String(20),default="web")
    status=mapped_column(String(30),default="PENDING")
    repeats=mapped_column(Integer,default=0)
    created_at=mapped_column(DateTime(timezone=True),default=now)
    sent_at=mapped_column(DateTime(timezone=True),nullable=True)
    next_allowed=mapped_column(DateTime(timezone=True))
    expires_at=mapped_column(DateTime(timezone=True))
    reply_id=mapped_column(String(36),nullable=True)

class InitiativeDecision(Base):
    __tablename__='initiative_decisions'
    source_key: Mapped[str]=mapped_column(String(180),primary_key=True)
    status: Mapped[str]=mapped_column(String(20),index=True)
    confidence: Mapped[float | None]=mapped_column(Float,nullable=True)
    model: Mapped[str]=mapped_column(String(160))
    context_hash: Mapped[str]=mapped_column(String(64))
    retry_after: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Delivery(Base):
    __tablename__="deliveries"
    id=mapped_column(String(36),primary_key=True,default=uid)
    dedup_key=mapped_column(String(180),unique=True)
    initiative_id=mapped_column(ForeignKey("initiatives.id"),nullable=True)
    conversation_id=mapped_column(ForeignKey("conversations.id"),nullable=True)
    kind=mapped_column(String(30),default="notification")
    channel=mapped_column(String(20))
    payload=mapped_column(JSON)
    status=mapped_column(String(30),default="PENDING")
    external_id=mapped_column(String(60),nullable=True)
    attempts=mapped_column(Integer,default=0)
    created_at=mapped_column(DateTime(timezone=True),default=now)
    sent_at=mapped_column(DateTime(timezone=True),nullable=True)
    lease_until=mapped_column(DateTime(timezone=True),nullable=True)
    error=mapped_column(String(150),nullable=True)

class TelegramBinding(Base):
    __tablename__="telegram_binding"
    id=mapped_column(Integer,primary_key=True)
    user_id=mapped_column(String(30),nullable=True)
    chat_id=mapped_column(String(30),nullable=True)
    conversation_id=mapped_column(ForeignKey("conversations.id"),nullable=True)
    code_hash=mapped_column(String(64),nullable=True)
    code_expires=mapped_column(DateTime(timezone=True),nullable=True)
    update_offset=mapped_column(Integer,default=0)

class ChannelUpdate(Base):
    __tablename__="channel_updates"
    id=mapped_column(String(60),primary_key=True)
    created_at=mapped_column(DateTime(timezone=True),default=now)

class Event(Base):
    __tablename__="events"
    id=mapped_column(String(36),primary_key=True,default=uid)
    key=mapped_column(String(150),unique=True)
    kind=mapped_column(String(50))
    details=mapped_column(JSON)
    created_at=mapped_column(DateTime(timezone=True),default=now)

class Skill(Base):
    __tablename__="skills"
    id=mapped_column(String(80),primary_key=True)
    version=mapped_column(String(30))
    manifest=mapped_column(JSON)
    enabled=mapped_column(Boolean,default=False)
    installed_at=mapped_column(DateTime(timezone=True),default=now)
    health=mapped_column(String(100),default="NOT_CHECKED")

class ConnectorSource(Base):
    __tablename__="connector_sources"
    id=mapped_column(String(41),primary_key=True,nullable=False)
    name=mapped_column(String(100),nullable=False)
    origin=mapped_column(String(300),nullable=False)
    recipe=mapped_column(JSON,nullable=False)
    enabled=mapped_column(Boolean,default=True,nullable=False)
    created_at=mapped_column(DateTime(timezone=True),default=now,nullable=False)
    schedule_minutes=mapped_column(Integer,nullable=True)
    next_run_at=mapped_column(DateTime(timezone=True),nullable=True)
    last_run_at=mapped_column(DateTime(timezone=True),nullable=True)
    last_run_status=mapped_column(String(20),nullable=True)
    schedule_fingerprint=mapped_column(String(64),nullable=True)
    schedule_expires_at=mapped_column(DateTime(timezone=True),nullable=True)
    session_ciphertext=mapped_column(Text,nullable=True)
    session_fingerprint=mapped_column(String(64),nullable=True)
    session_generation=mapped_column(String(36),nullable=True)
    session_expires_at=mapped_column(DateTime(timezone=True),nullable=True)
    session_kind=mapped_column(String(16),nullable=True)
    fetch_mode=mapped_column(String(16),nullable=False,default='http')

class ConnectorAction(Base):
    __tablename__='connector_actions'
    id=mapped_column(String(36),primary_key=True,default=uid)
    connector_id=mapped_column(ForeignKey('connector_sources.id',ondelete='CASCADE'),index=True,nullable=False)
    name=mapped_column(String(100),nullable=False)
    method=mapped_column(String(8),nullable=False)
    target_url=mapped_column(String(500),nullable=False)
    body=mapped_column(JSON,nullable=False)
    verification_url=mapped_column(String(500),nullable=False)
    verification_field=mapped_column(String(80),nullable=False)
    expected_value=mapped_column(String(500),nullable=False)
    csrf_cookie_name=mapped_column(String(128),nullable=True)
    csrf_header_name=mapped_column(String(30),nullable=True)
    interval_minutes=mapped_column(Integer,nullable=True)
    next_run_at=mapped_column(DateTime(timezone=True),nullable=True)
    expires_at=mapped_column(DateTime(timezone=True),nullable=False)
    scope_fingerprint=mapped_column(String(64),nullable=False)
    enabled=mapped_column(Boolean,default=True,nullable=False)
    auto_approved=mapped_column(Boolean,default=False,nullable=False)
    approval_expires_at=mapped_column(DateTime(timezone=True),nullable=True)
    max_runs=mapped_column(Integer,nullable=True)
    run_count=mapped_column(Integer,default=0,nullable=False)
    created_at=mapped_column(DateTime(timezone=True),default=now,nullable=False)

class ConnectorActionRun(Base):
    __tablename__='connector_action_runs'
    id=mapped_column(String(36),primary_key=True,default=uid)
    action_id=mapped_column(ForeignKey('connector_actions.id',ondelete='CASCADE'),index=True,nullable=False)
    status=mapped_column(String(30),nullable=False)
    request_fingerprint=mapped_column(String(64),nullable=False)
    auto_authorized=mapped_column(Boolean,default=False,nullable=False)
    scheduled_for=mapped_column(DateTime(timezone=True),nullable=False)
    approved_at=mapped_column(DateTime(timezone=True),nullable=True)
    started_at=mapped_column(DateTime(timezone=True),nullable=True)
    completed_at=mapped_column(DateTime(timezone=True),nullable=True)
    error_code=mapped_column(String(60),nullable=True)
    created_at=mapped_column(DateTime(timezone=True),default=now,nullable=False)

class ConnectorRecord(Base):
    __tablename__="connector_records"
    __table_args__=(UniqueConstraint("connector_id","record_key"),)
    id=mapped_column(String(36),primary_key=True,default=uid,nullable=False)
    connector_id=mapped_column(ForeignKey("connector_sources.id",ondelete="CASCADE"),index=True,nullable=False)
    record_key=mapped_column(String(200),nullable=False)
    data=mapped_column(JSON,nullable=False)
    imported_at=mapped_column(DateTime(timezone=True),default=now,nullable=False)

class DemoHomework(Base):
    __tablename__="demo_homework"
    id=mapped_column(String(80),primary_key=True)
    subject=mapped_column(String(100))
    title=mapped_column(String(300))
    due_at=mapped_column(DateTime(timezone=True))
    grade=mapped_column(String(20),nullable=True)

class Assignment(Base):
    __tablename__="assignments"
    __table_args__=(UniqueConstraint("provider","external_id"),)
    id=mapped_column(String(36),primary_key=True,default=uid)
    provider=mapped_column(String(80))
    external_id=mapped_column(String(80))
    subject=mapped_column(String(100))
    title=mapped_column(String(300))
    due_at=mapped_column(DateTime(timezone=True))
    grade=mapped_column(String(20),nullable=True)
    source=mapped_column(String(200))
    content_hash=mapped_column(String(64))
    synced_at=mapped_column(DateTime(timezone=True),default=now)
    revision=mapped_column(Integer,default=1)

class StudyTask(Base):
    __tablename__="study_tasks"
    id=mapped_column(String(36),primary_key=True,default=uid)
    assignment_id=mapped_column(ForeignKey("assignments.id"),unique=True)
    goal=mapped_column(String(400))
    due_at=mapped_column(DateTime(timezone=True))
    status=mapped_column(String(30),default="PENDING")


class SchoolScheduleDay(Base):
    __tablename__='school_schedule_days'
    __table_args__=(UniqueConstraint('provider','day'),)
    id=mapped_column(String(36),primary_key=True,default=uid)
    provider=mapped_column(String(80),nullable=False)
    day=mapped_column(Date,nullable=False,index=True)
    lessons=mapped_column(JSON,nullable=False)
    content_hash=mapped_column(String(64),nullable=False)
    source=mapped_column(String(200),nullable=False)
    synced_at=mapped_column(DateTime(timezone=True),default=now,nullable=False)
    revision=mapped_column(Integer,default=1,nullable=False)


class Device(Base):
    __tablename__='devices'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    name: Mapped[str]=mapped_column(String(100))
    fingerprint: Mapped[str]=mapped_column(String(64),unique=True)
    enabled: Mapped[bool]=mapped_column(Boolean,default=False)
    roots: Mapped[list]=mapped_column(JSON,default=list)
    operations: Mapped[list]=mapped_column(JSON,default=list)
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    voice_enabled: Mapped[bool]=mapped_column(Boolean,default=False,nullable=False)
    voice_conversation_id: Mapped[str | None]=mapped_column(ForeignKey('conversations.id',ondelete='SET NULL',name='fk_devices_voice_conversation_id_conversations'),nullable=True)

class DeviceCommand(Base):
    __tablename__='device_commands'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    device_id: Mapped[str]=mapped_column(ForeignKey('devices.id'),index=True)
    call_id: Mapped[str]=mapped_column(ForeignKey('tool_calls.id'),unique=True)
    arguments: Mapped[dict]=mapped_column(JSON)
    status: Mapped[str]=mapped_column(String(30),default='PENDING')
    lease_token: Mapped[str | None]=mapped_column(String(64),nullable=True)
    lease_until: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None]=mapped_column(JSON,nullable=True)
    error: Mapped[str | None]=mapped_column(String(150),nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class DeviceMutex(Base):
    __tablename__='device_mutex'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)


class VoiceStation(Base):
    __tablename__ = 'voice_stations'
    device_id: Mapped[str] = mapped_column(ForeignKey('devices.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class VoiceControl(Base):
    __tablename__ = 'voice_controls'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey('devices.id'), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='PENDING')
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class VoiceEvent(Base):
    __tablename__ = 'voice_events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey('devices.id'), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class VoiceRequest(Base):
    __tablename__ = 'voice_requests'
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey('devices.id'), index=True)
    keep_text: Mapped[bool] = mapped_column(Boolean, default=True)
    digest: Mapped[str] = mapped_column(String(64))
    context: Mapped[list] = mapped_column(JSON, default=list)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemoryFact(Base):
    __tablename__='memory_facts'
    memory_id: Mapped[str]=mapped_column(ForeignKey('memories.id'),primary_key=True)
    key: Mapped[str]=mapped_column(String(100),index=True)
    value: Mapped[str]=mapped_column(String(1000))
    active: Mapped[bool]=mapped_column(Boolean,default=True)
class MemoryConflict(Base):
    __tablename__='memory_conflicts'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    left_id: Mapped[str]=mapped_column(ForeignKey('memories.id'))
    right_id: Mapped[str]=mapped_column(ForeignKey('memories.id'))
    status: Mapped[str]=mapped_column(String(30),default='PENDING')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects.postgresql.base import ischema_names
class PublicVector(VECTOR):
    cache_ok=True
    def get_col_spec(self,**kw):return f'public.vector({self.dim})'
ischema_names['public.vector']=PublicVector
class Embedding(Base):
    __tablename__='embeddings'
    key: Mapped[str]=mapped_column(String(64),primary_key=True)
    memory_id: Mapped[str | None]=mapped_column(ForeignKey('memories.id'),nullable=True,index=True)
    assignment_id: Mapped[str | None]=mapped_column(ForeignKey('assignments.id', ondelete='CASCADE'),nullable=True,index=True)
    model: Mapped[str]=mapped_column(String(200))
    digest: Mapped[str]=mapped_column(String(64))
    vector: Mapped[list]=mapped_column(PublicVector(768).with_variant(JSON(),'sqlite'))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class Attachment(Base):
    image: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warnings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    original: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __tablename__='attachments'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    name: Mapped[str]=mapped_column(String(100))
    content: Mapped[str]=mapped_column(Text)
    sha256: Mapped[str]=mapped_column(String(64))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class Budget(Base):
    __tablename__='budget'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    token_limit: Mapped[int]=mapped_column(Integer,default=1000000)
    cost_limit: Mapped[int | None]=mapped_column(Integer,nullable=True)
    input_rate: Mapped[int]=mapped_column(Integer,default=0)
    output_rate: Mapped[int]=mapped_column(Integer,default=0)
    daily_token_limit: Mapped[int | None]=mapped_column(Integer,nullable=True)
    daily_cost_limit: Mapped[int | None]=mapped_column(Integer,nullable=True)
    task_cost_limit: Mapped[int | None]=mapped_column(Integer,nullable=True)
    speech_rub_month_limit: Mapped[int | None]=mapped_column(Integer,nullable=True)
    speech_rub_day_limit: Mapped[int | None]=mapped_column(Integer,nullable=True)
class ModelUsage(Base):
    __tablename__='model_usage'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    purpose: Mapped[str]=mapped_column(String(30))
    provider: Mapped[str]=mapped_column(String(100))
    status: Mapped[str]=mapped_column(String(30),default='RESERVED')
    tokens: Mapped[int]=mapped_column(Integer)
    cost: Mapped[int | None]=mapped_column(Integer,nullable=True)
    task_id: Mapped[str | None]=mapped_column(String(36),nullable=True,index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class SpeechUsage(Base):
    __tablename__='speech_usage'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    provider: Mapped[str]=mapped_column(String(100))
    status: Mapped[str]=mapped_column(String(30),default='RESERVED')
    duration_seconds: Mapped[int]=mapped_column(Integer)
    cost_kopecks: Mapped[int]=mapped_column(Integer)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,index=True)
class PushSubscription(Base):
    __tablename__='push_subscriptions'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    endpoint: Mapped[str]=mapped_column(Text,unique=True)
    keys: Mapped[dict]=mapped_column(JSON)
    enabled: Mapped[bool]=mapped_column(Boolean,default=True)
class PushDelivery(Base):
    __tablename__='push_deliveries'
    __table_args__=(UniqueConstraint('initiative_id','subscription_id'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    initiative_id: Mapped[str]=mapped_column(ForeignKey('initiatives.id'))
    subscription_id: Mapped[str]=mapped_column(ForeignKey('push_subscriptions.id'))
    status: Mapped[str]=mapped_column(String(30),default='PENDING')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class OwnerSession(Base):
    __tablename__='owner_sessions'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool]=mapped_column(Boolean,default=False)
class ServiceHeartbeat(Base):
    __tablename__='service_heartbeats'
    name: Mapped[str]=mapped_column(String(30),primary_key=True)
    seen_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class ModelPreferences(Base):
    __tablename__='model_preferences'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    revision: Mapped[int]=mapped_column(Integer,default=1)
    config: Mapped[dict]=mapped_column(JSON)

class Space(Base):
    __tablename__ = 'spaces'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(1000), default='')
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class SpaceConversation(Base):
    __tablename__ = 'space_conversations'
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey('spaces.id'), index=True)

class SpaceMemory(Base):
    __tablename__ = 'space_memories'
    space_id: Mapped[str] = mapped_column(ForeignKey('spaces.id'), primary_key=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey('memories.id', ondelete='CASCADE'), primary_key=True)
