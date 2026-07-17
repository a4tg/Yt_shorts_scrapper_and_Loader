import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, false, func, true
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("credit_balance >= 0", name="ck_users_credit_balance_nonnegative"),
        CheckConstraint("reserved_credits >= 0", name="ck_users_reserved_credits_nonnegative"),
        CheckConstraint("reserved_credits <= credit_balance", name="ck_users_reserved_within_balance"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", index=True)
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credit_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trial_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", index=True)
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members_workspace_user"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'editor', 'viewer', 'client')",
            name="ck_workspace_members_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="viewer", index=True)
    invited_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_projects_workspace_slug"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#7c6cff")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", index=True)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )


class ApprovalWorkflow(TimestampMixin, Base):
    __tablename__ = "approval_workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="Основной процесс")


class ApprovalStage(Base):
    __tablename__ = "approval_stages"
    __table_args__ = (
        UniqueConstraint("workflow_id", "position", name="uq_approval_stages_position"),
        UniqueConstraint("workflow_id", "stage_key", name="uq_approval_stages_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("approval_workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#7c6cff")
    required_role: Mapped[str | None] = mapped_column(String(24))
    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ContentItem(TimestampMixin, Base):
    __tablename__ = "content_items"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source_platform", "source_id", name="uq_content_items_external_source"
        ),
        CheckConstraint(
            "item_type IN ('post', 'video', 'banner', 'document', 'campaign', 'note')",
            name="ck_content_items_type",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_content_items_priority",
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_content_items_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_id: Mapped[str | None] = mapped_column(
        ForeignKey("approval_stages.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    item_type: Mapped[str] = mapped_column(String(24), nullable=False, default="post", index=True)
    body: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(String(80), index=True)
    source_platform: Mapped[str | None] = mapped_column(String(24), index=True)
    source_id: Mapped[str | None] = mapped_column(String(160), index=True)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    tags: Mapped[list[str] | None] = mapped_column(JSON)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal", index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    assignee_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ContentRevision(Base):
    __tablename__ = "content_revisions"
    __table_args__ = (
        UniqueConstraint("content_item_id", "version_number", name="uq_content_revisions_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    content_item_id: Mapped[str] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    changed_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class ProjectFolder(TimestampMixin, Base):
    __tablename__ = "project_folders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_folders.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )


class ContentAttachment(Base):
    __tablename__ = "content_attachments"
    __table_args__ = (
        UniqueConstraint("asset_key", "version_number", name="uq_content_attachment_asset_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    folder_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_folders.id", ondelete="SET NULL"), index=True
    )
    content_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="SET NULL"), index=True
    )
    uploaded_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False, unique=True)
    mime_type: Mapped[str | None] = mapped_column(String(160))
    source_type: Mapped[str] = mapped_column(String(24), nullable=False, default="upload", index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    asset_key: Mapped[str] = mapped_column(String(36), nullable=False, default=new_id, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version_label: Mapped[str | None] = mapped_column(String(120))
    version_notes: Mapped[str | None] = mapped_column(Text)
    supersedes_attachment_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_attachments.id", ondelete="SET NULL"), index=True
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class AssetReview(TimestampMixin, Base):
    __tablename__ = "asset_reviews"
    __table_args__ = (
        CheckConstraint(
            "annotation_type IN ('general', 'point', 'region', 'timestamp', 'page', 'drawing')",
            name="ck_asset_reviews_annotation_type",
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved', 'wont_fix')",
            name="ck_asset_reviews_status",
        ),
        CheckConstraint("visibility IN ('team', 'client')", name="ck_asset_reviews_visibility"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attachment_id: Mapped[str] = mapped_column(
        ForeignKey("content_attachments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_review_id: Mapped[str | None] = mapped_column(
        ForeignKey("asset_reviews.id", ondelete="CASCADE"), index=True
    )
    author_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assignee_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    annotation_type: Mapped[str] = mapped_column(String(24), nullable=False, default="general")
    x: Mapped[float | None] = mapped_column(Float)
    y: Mapped[float | None] = mapped_column(Float)
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    time_seconds: Mapped[float | None] = mapped_column(Float)
    page_number: Mapped[int | None] = mapped_column(Integer)
    annotation_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", index=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="team", index=True)
    resolved_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class AssetApproval(Base):
    __tablename__ = "asset_approvals"
    __table_args__ = (
        UniqueConstraint("attachment_id", "user_id", name="uq_asset_approval_user"),
        CheckConstraint(
            "decision IN ('approved', 'changes_requested')", name="ck_asset_approvals_decision"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attachment_id: Mapped[str] = mapped_column(
        ForeignKey("content_attachments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class EntityLink(TimestampMixin, Base):
    __tablename__ = "entity_links"
    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('relates_to', 'depends_on', 'blocks', 'produces', 'references', 'assigned_to', 'custom')",
            name="ck_entity_links_relation_type",
        ),
        UniqueConstraint(
            "project_id", "source_type", "source_id", "target_type", "target_id", "relation_type",
            name="uq_entity_links_relation",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False, default="relates_to", index=True)
    label: Mapped[str | None] = mapped_column(String(160))
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )


class ProjectDiagram(TimestampMixin, Base):
    __tablename__ = "project_diagrams"
    __table_args__ = (
        CheckConstraint("diagram_type IN ('process', 'flowchart', 'mind_map')", name="ck_project_diagrams_type"),
        CheckConstraint("visibility IN ('team', 'client')", name="ck_project_diagrams_visibility"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    diagram_type: Mapped[str] = mapped_column(String(24), nullable=False, default="flowchart", index=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="team", index=True)
    viewport: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )


class DiagramNode(TimestampMixin, Base):
    __tablename__ = "diagram_nodes"
    __table_args__ = (
        UniqueConstraint("diagram_id", "node_key", name="uq_diagram_nodes_key"),
        CheckConstraint(
            "kind IN ('start', 'end', 'task', 'decision', 'document', 'asset', 'person', 'note')",
            name="ck_diagram_nodes_kind",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    diagram_id: Mapped[str] = mapped_column(
        ForeignKey("project_diagrams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_key: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="task", index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))
    x: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    y: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    width: Mapped[float] = mapped_column(Float, nullable=False, default=180)
    height: Mapped[float] = mapped_column(Float, nullable=False, default=80)
    color: Mapped[str | None] = mapped_column(String(16))
    entity_type: Mapped[str | None] = mapped_column(String(32), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), index=True)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class DiagramEdge(Base):
    __tablename__ = "diagram_edges"
    __table_args__ = (
        CheckConstraint(
            "edge_type IN ('default', 'success', 'failure', 'conditional')",
            name="ck_diagram_edges_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    diagram_id: Mapped[str] = mapped_column(
        ForeignKey("project_diagrams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_node_id: Mapped[str] = mapped_column(
        ForeignKey("diagram_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_node_id: Mapped[str] = mapped_column(
        ForeignKey("diagram_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str | None] = mapped_column(String(160))
    edge_type: Mapped[str] = mapped_column(String(24), nullable=False, default="default", index=True)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ProjectInsight(TimestampMixin, Base):
    __tablename__ = "project_insights"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('decision', 'commitment', 'action', 'risk', 'question')",
            name="ck_project_insights_kind",
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'done', 'dismissed')",
            name="ck_project_insights_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_project_insights_priority",
        ),
        CheckConstraint("visibility IN ('team', 'client')", name="ck_project_insights_visibility"),
        UniqueConstraint("project_id", "fingerprint", name="uq_project_insights_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal", index=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="team", index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source_excerpt: Mapped[str | None] = mapped_column(String(1000))
    assignee_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    impact_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    completed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class InsightLink(Base):
    __tablename__ = "insight_links"
    __table_args__ = (
        UniqueConstraint("insight_id", "entity_type", "entity_id", "relation_type", name="uq_insight_links_relation"),
        CheckConstraint(
            "relation_type IN ('derived_from', 'impacts', 'depends_on', 'resolves')",
            name="ck_insight_links_relation_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    insight_id: Mapped[str] = mapped_column(
        ForeignKey("project_insights.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)


class ProjectBriefing(Base):
    __tablename__ = "project_briefings"
    __table_args__ = (
        CheckConstraint("visibility IN ('team', 'client')", name="ck_project_briefings_visibility"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    highlights: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    risks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    next_actions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="team", index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="rules")
    model: Mapped[str | None] = mapped_column(String(120))
    source_stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    generated_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("project_id", "conversation_key", name="uq_conversations_project_key"),
        CheckConstraint(
            "kind IN ('group', 'direct', 'context')", name="ck_conversations_kind"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    conversation_key: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str | None] = mapped_column(String(120))
    is_project_wide: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )


class ConversationParticipant(Base):
    __tablename__ = "conversation_participants"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_conversation_participant_user"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reply_to_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), index=True
    )
    attachment_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_attachments.id", ondelete="SET NULL"), index=True
    )
    attachment_name: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    mentioned_user_ids: Mapped[list[str] | None] = mapped_column(JSON)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    pinned_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class MessageReaction(Base):
    __tablename__ = "message_reactions"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", "emoji", name="uq_message_reaction_user_emoji"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    emoji: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class AccountToken(Base):
    __tablename__ = "account_tokens"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('verify_email', 'reset_password')",
            name="ck_account_tokens_purpose",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Plan(TimestampMixin, Base):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("monthly_credits >= 0", name="ck_plans_monthly_credits_nonnegative"),
        CheckConstraint("price_minor >= 0", name="ck_plans_price_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    monthly_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feature_limits: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Subscription(TimestampMixin, Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(40))
    provider_subscription_id: Mapped[str | None] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_method_id: Mapped[str | None] = mapped_column(String(160))
    renewal_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="ck_payments_amount_nonnegative"),
        CheckConstraint("credits >= 0", name="ck_payments_credits_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id"), index=True)
    subscription_id: Mapped[str | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(String(160), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmation_url: Mapped[str | None] = mapped_column(Text)
    billing_period_key: Mapped[str | None] = mapped_column(
        String(160), unique=True, index=True
    )
    provider_payment_method_id: Mapped[str | None] = mapped_column(String(160))
    provider_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    event_key: Mapped[str] = mapped_column(String(240), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    object_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    source_ip: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received", index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeedbackTicket(TimestampMixin, Base):
    __tablename__ = "feedback_tickets"
    __table_args__ = (
        CheckConstraint(
            "category IN ('bug', 'idea', 'question', 'billing')",
            name="ck_feedback_tickets_category",
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved', 'closed')",
            name="ck_feedback_tickets_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    category: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    page: Mapped[str | None] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", index=True)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ProductEvent(Base):
    __tablename__ = "product_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    event_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    page: Mapped[str | None] = mapped_column(String(40), index=True)
    properties: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("credits_reserved >= 0", name="ck_jobs_reserved_nonnegative"),
        CheckConstraint("credits_spent >= 0", name="ck_jobs_spent_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    message: Mapped[str | None] = mapped_column(String(1000))
    error_message: Mapped[str | None] = mapped_column(Text)
    credits_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    credits_spent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(80), index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    download_ticket_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class JobFile(Base):
    __tablename__ = "job_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False, unique=True)
    mime_type: Mapped[str | None] = mapped_column(String(160))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Overlay(Base):
    __tablename__ = "overlays"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False, unique=True)
    mime_type: Mapped[str | None] = mapped_column(String(160))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class CreditLedger(Base):
    __tablename__ = "credit_ledger"
    __table_args__ = (
        CheckConstraint("amount != 0", name="ck_credit_ledger_amount_nonzero"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    payment_id: Mapped[str | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL"), index=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(160), unique=True, index=True
    )
    description: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
