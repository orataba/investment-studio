from __future__ import annotations

from dataclasses import dataclass


CALCULATION_REGISTRY_SCHEMA = "calculation_registry"
type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class DatabaseFunctionArgument:
    name: str
    database_type: str


@dataclass(frozen=True, slots=True)
class DatabaseFunctionSignature:
    qualified_name: str
    arguments: tuple[DatabaseFunctionArgument, ...]
    return_type: str

    @property
    def argument_types(self) -> tuple[str, ...]:
        return tuple(argument.database_type for argument in self.arguments)


@dataclass(frozen=True, slots=True)
class ProducerOutputFenceContract:
    output_column_name: str
    output_database_type: str
    publication_column_name: str
    required_identity_columns: tuple[str, ...]
    canonical_hash_excluded_columns: frozenset[str]
    write_guard: DatabaseFunctionSignature


ASSERT_MANIFEST_BUILDING = DatabaseFunctionSignature(
    qualified_name="calculation_registry.assert_manifest_building",
    arguments=(DatabaseFunctionArgument("p_manifest_id", "uuid"),),
    return_type="void",
)
GUARD_MANIFEST_DEPENDENCY_MUTATION = DatabaseFunctionSignature(
    qualified_name="calculation_registry.guard_manifest_dependency_mutation",
    arguments=(),
    return_type="trigger",
)
ASSERT_RUN_OUTPUT_WRITABLE = DatabaseFunctionSignature(
    qualified_name="calculation_registry.assert_run_output_writable",
    arguments=(
        DatabaseFunctionArgument("p_run_id", "uuid"),
        DatabaseFunctionArgument("p_fencing_token", "bigint"),
        DatabaseFunctionArgument("p_lease_owner", "varchar"),
    ),
    return_type="void",
)
REJECT_TRUNCATE = DatabaseFunctionSignature(
    qualified_name="calculation_registry.reject_truncate",
    arguments=(),
    return_type="trigger",
)
VALIDATE_CURRENT_PUBLICATION_COMMIT = DatabaseFunctionSignature(
    qualified_name="calculation_registry.validate_current_publication_commit",
    arguments=(),
    return_type="trigger",
)
ROUND_HALF_EVEN = DatabaseFunctionSignature(
    qualified_name="calculation_registry.round_half_even",
    arguments=(
        DatabaseFunctionArgument("p_value", "numeric"),
        DatabaseFunctionArgument("p_scale", "integer"),
    ),
    return_type="numeric",
)
CURRENT_PUBLICATION_COMMIT_TRIGGER = "trg_calculation_current_publication_commit"

# Every producer output table must make the attempt fence part of row identity.
# Publication and readers select exactly this token; it is lineage, not part of
# the canonical financial output hash.
PRODUCER_OUTPUT_FENCE = ProducerOutputFenceContract(
    output_column_name="output_fencing_token",
    output_database_type="bigint",
    publication_column_name="published_fencing_token",
    required_identity_columns=("run_id", "output_fencing_token"),
    canonical_hash_excluded_columns=frozenset({"output_fencing_token"}),
    write_guard=ASSERT_RUN_OUTPUT_WRITABLE,
)
