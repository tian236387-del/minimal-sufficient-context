from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    model: Optional[str] = Field(default=None, min_length=1, max_length=200)
    system_prompt: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=200_000,
    )
    token_budget: int = Field(default=8192, ge=256, le=262_144)


class ConversationUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    token_budget: Optional[int] = Field(default=None, ge=256, le=262_144)
    active_branch_id: Optional[int] = Field(default=None, gt=0)
    active_message_id: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_update(self):
        if not self.model_fields_set:
            raise ValueError("At least one conversation field is required")
        if "active_message_id" in self.model_fields_set and (
            "active_branch_id" not in self.model_fields_set
        ):
            raise ValueError("active_branch_id is required with active_message_id")
        return self


class BranchCreate(BaseModel):
    forked_from_message_id: Optional[int] = Field(default=None, gt=0)
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)


class BranchUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ChatRequest(BaseModel):
    conversation_id: int = Field(gt=0)
    branch_id: Optional[int] = Field(default=None, gt=0)
    parent_id: Optional[int] = Field(default=None, gt=0)
    content: str = Field(min_length=1, max_length=200_000)
    model: Optional[str] = Field(default=None, min_length=1, max_length=200)


class ContextCompareRequest(BaseModel):
    conversation_id: int = Field(gt=0)
    branch_id: Optional[int] = Field(default=None, gt=0)
    parent_id: Optional[int] = Field(default=None, gt=0)
    content: str = Field(min_length=1, max_length=200_000)
    model: Optional[str] = Field(default=None, min_length=1, max_length=200)
    token_budget: Optional[int] = Field(default=None, ge=256, le=262_144)


class SummaryClaimCreate(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=2_000)
    claim_text: Optional[str] = Field(default=None, max_length=2_000)
    source_message_id: Optional[int] = Field(default=None, gt=0)


class SummaryCreate(BaseModel):
    branch_id: Optional[int] = Field(default=None, gt=0)
    anchor_message_id: Optional[int] = Field(default=None, gt=0)
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    content: Optional[str] = Field(default=None, min_length=1, max_length=50_000)
    source_message_ids: list[int] = Field(default_factory=list, min_length=0)
    claims: list[SummaryClaimCreate] = Field(default_factory=list)


class MergePreviewRequest(BaseModel):
    target_branch_id: int = Field(gt=0)
    source_branch_id: int = Field(gt=0)
    target_summary_ids: list[int] = Field(default_factory=list)
    source_summary_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_distinct_branches(self):
        if self.target_branch_id == self.source_branch_id:
            raise ValueError("target_branch_id and source_branch_id must differ")
        return self


class MergeCreate(MergePreviewRequest):
    preview_token: Optional[str] = Field(default=None, min_length=8, max_length=128)
    resolutions: dict[str, str] = Field(default_factory=dict)
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    activate: bool = False


class MergeRollback(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=2_000)
