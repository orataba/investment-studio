"""Portfolio-owned concentration limits and FCN management allocations."""
from datetime import date
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConcentrationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str = Field(min_length=1, max_length=200)
    scope: Literal["security", "fcn", "taxonomy"]
    taxonomy_id: str | None = None
    entity_id: str | None = None
    watch_weight: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    limit_weight: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_rule(self):
        if (self.scope == "taxonomy") != bool(self.taxonomy_id):
            raise ValueError("Only taxonomy limits require a taxonomy_id.")
        if self.watch_weight is not None and self.limit_weight is not None and self.watch_weight > self.limit_weight:
            raise ValueError("The watch threshold must not exceed the limit.")
        if self.entity_id is not None and not self.entity_id.strip():
            raise ValueError("entity_id must be non-empty or null for the default limit.")
        return self


class PrincipalAllocationWeight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str = Field(min_length=1)
    weight: float = Field(ge=0, le=1, allow_inf_nan=False)


class FcnPrincipalAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_id: str = Field(min_length=1)
    method: Literal["equal", "custom"] = "equal"
    weights: list[PrincipalAllocationWeight] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_weights(self):
        ids = [row.instrument_id for row in self.weights]
        if len(ids) != len(set(ids)):
            raise ValueError("Allocation instruments must be unique.")
        if self.method == "equal" and self.weights:
            raise ValueError("Equal allocation does not accept custom weights.")
        if self.method == "custom" and (not self.weights or not isfinite(sum(row.weight for row in self.weights)) or abs(sum(row.weight for row in self.weights) - 1) > 1e-9):
            raise ValueError("Custom FCN allocation weights must total 100%.")
        return self


class ConcentrationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    effective_from: date
    rules: list[ConcentrationRule] = Field(default_factory=list)
    fcn_allocations: list[FcnPrincipalAllocation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_rules(self):
        keys = [(row.scope, row.taxonomy_id, row.entity_id) for row in self.rules]
        ids = [row.rule_id for row in self.rules]
        contracts = [row.contract_id for row in self.fcn_allocations]
        if len(keys) != len(set(keys)) or len(ids) != len(set(ids)):
            raise ValueError("Use one concentration rule per scope and entity, with unique rule IDs.")
        if len(contracts) != len(set(contracts)):
            raise ValueError("Use one allocation per FCN contract.")
        return self
