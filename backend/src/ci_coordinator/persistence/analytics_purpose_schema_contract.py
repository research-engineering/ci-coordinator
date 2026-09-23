from typing import Final

from sqlalchemy.engine import Connection

from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_economics_schema_contract import EconomicsCatalogContract

_TABLE = "analytics_purpose_settings"
_DEFINITIONS = {
    (_TABLE, "pk_analytics_purpose_settings"): "PRIMARY KEY (installation_id, repository_id)",
    (
        _TABLE,
        "fk_analytics_purpose_dataset",
    ): "FOREIGN KEY (installation_id, repository_id) REFERENCES "
    "ci_coordinator.ci_history_datasets(installation_id, repository_id) ON DELETE RESTRICT",
    (
        _TABLE,
        "ck_analytics_purpose_revision",
    ): "CHECK (generation >= 1 AND generation <= '9007199254740991'::bigint "
    "AND revision >= 1 AND revision <= '9007199254740991'::bigint)",
    (
        _TABLE,
        "ck_analytics_purpose_payload",
    ): "CHECK (octet_length(snapshot_canonical) >= 1 "
    "AND octet_length(snapshot_canonical) <= 262144)",
}
PURPOSE_CATALOG: Final = EconomicsCatalogContract(
    relations=(_TABLE,),
    columns=tuple(
        (_TABLE, column, kind, False, None)
        for column, kind in (
            ("installation_id", "bigint"),
            ("repository_id", "bigint"),
            ("generation", "bigint"),
            ("revision", "bigint"),
            ("snapshot_canonical", "bytea"),
        )
    ),
    constraints=frozenset(
        (_TABLE, name, kind, False, False, True, True)
        for name, kind in (
            ("pk_analytics_purpose_settings", "p"),
            ("fk_analytics_purpose_dataset", "f"),
            ("ck_analytics_purpose_revision", "c"),
            ("ck_analytics_purpose_payload", "c"),
        )
    ),
    constraint_definitions=_DEFINITIONS,
    indexes=frozenset(),
    index_definitions={},
    tables=((_TABLE, "r", "p", False, False, True, True),),
    triggers=(),
    rewrite_rules=(),
    routine_source=None,
)


def analytics_purpose_schema_matches(connection: Connection) -> bool:
    return ci_economics_schema_matches_contract_sync(connection, contract=PURPOSE_CATALOG)
