"""SQLModel table classes for regin.

Every class under this package is imported here so that
`lib.orm.base.metadata` sees them at module-load time — Alembic's
autogenerate relies on that single MetaData collection.
"""

from __future__ import annotations

from lib.orm.models.agent_messages import AgentMessage
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
)
from lib.orm.models.rules import Experiment, RuleTrigger, RuleTriggerSuppression
from lib.orm.models.sync import Branch, Repo
from lib.orm.models.trace import (
    PlanSession, PromptImage, Session, SessionRepo, SessionSpan,
    SessionTraceMap, SkillRead, TurnUsage,
)
from lib.orm.models.users import AuditLog, User

__all__ = [
    "AgentMessage",
    "SessionGrade",
    "User", "AuditLog",
    "Repo", "Branch",
    "PatternDoc", "Tag", "DocTag", "PatternDeployment", "PatternEmbedding",
    "PayloadSchemaDrift",
    "PromptTemplate",
    "ProposalRun", "ProposalTopic", "ProposalRevision", "ProposalRevisionTopic",
    "ProposalFeedbackThread", "ProposalFeedbackComment",
    "GraphSnapshot", "TopicAudit",
    "RuleTrigger", "RuleTriggerSuppression", "Experiment",
    "SessionSpan", "Session", "SkillRead", "PlanSession", "TurnUsage",
    "SessionRepo", "PromptImage",
]
