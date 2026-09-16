"""SQLModel table classes for regin.

Every class under this package is imported here so that
`lib.orm.base.metadata` sees them at module-load time — Alembic's
autogenerate relies on that single MetaData collection.
"""

from __future__ import annotations

from lib.orm.models.agent_messages import AgentMessage
from lib.orm.models.agent_runs import AgentRun, AgentRunSession
from lib.orm.models.api_keys import ApiKey
from lib.orm.models.comments import Comment, CommentThread
from lib.orm.models.defects import Defect
from lib.orm.models.features import SessionFeature
from lib.orm.models.grades import SessionGrade
from lib.orm.models.patterns import DocTag, PatternDeployment, PatternDoc, PatternEmbedding, Tag
from lib.orm.models.payload_schema_drift import PayloadSchemaDrift
from lib.orm.models.prompts import PromptTemplate
from lib.orm.models.proposals import (
    GraphSnapshot,
    ProposalFeedbackComment,
    ProposalFeedbackThread,
    ProposalRevision,
    ProposalRevisionTopic,
    ProposalRun,
    ProposalTopic,
    TopicAudit,
    TopicRefDigest,
)
from lib.orm.models.rules import Experiment, RuleTrigger, RuleTriggerSuppression
from lib.orm.models.features import Feature, FeatureEvent
from lib.orm.models.sync import Branch, Repo
from lib.orm.models.tasks import AgentTask
from lib.orm.models.trace import (
    BridgeAttachment, PlanSession, PromptImage, Session, SessionRepo,
    SessionSpan, SessionTag, SessionTraceMap, SkillRead, TurnUsage,
)
from lib.orm.models.users import AuditLog, User

__all__ = [
    "AgentMessage",
    "AgentRun",
    "AgentRunSession",
    "AgentTask",
    "ApiKey",
    "Comment",
    "CommentThread",
    "Defect",
    "Feature",
    "FeatureEvent",
    "SessionFeature",
    "SessionGrade",
    "User", "AuditLog",
    "Repo", "Branch",
    "PatternDoc", "Tag", "DocTag", "PatternDeployment", "PatternEmbedding",
    "PayloadSchemaDrift",
    "PromptTemplate",
    "ProposalRun", "ProposalTopic", "ProposalRevision", "ProposalRevisionTopic",
    "ProposalFeedbackThread", "ProposalFeedbackComment",
    "GraphSnapshot", "TopicAudit", "TopicRefDigest",
    "RuleTrigger", "RuleTriggerSuppression", "Experiment",
    "SessionSpan", "Session", "SkillRead", "PlanSession", "TurnUsage",
    "SessionRepo", "SessionTag", "PromptImage", "BridgeAttachment",
]
