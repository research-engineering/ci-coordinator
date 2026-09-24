from __future__ import annotations

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.planning_core.model import SelectedObligation, SelectedWitness
from ci_coordinator.validation_contract import ValidationCatalog, depth_rank


def close_selected_witnesses(
    catalog: ValidationCatalog,
    selected: tuple[SelectedObligation, ...],
) -> tuple[SelectedWitness, ...]:
    obligations = {item.obligation_id: item for item in catalog.obligations}
    witnesses = {item.witness_id: item for item in catalog.witnesses}
    required_by: dict[str, list[SelectedObligation]] = {}
    for selected_obligation in selected:
        obligation = obligations.get(selected_obligation.obligation_id)
        if (
            obligation is None
            or selected_obligation.required_witness_ids != obligation.required_witness_ids
        ):
            raise ValueError("selected obligation requirements must match the validation catalog")
        for witness_id in selected_obligation.required_witness_ids:
            witness = witnesses.get(witness_id)
            if witness is None or selected_obligation.depth not in witness.supported_depths:
                raise ValueError("selected depth is unsupported by a required witness")
            required_by.setdefault(witness_id, []).append(selected_obligation)
    return tuple(
        SelectedWitness(
            witness_id=witness_id,
            depth=max((item.depth for item in requirements), key=depth_rank),
            required_by_obligation_ids=tuple(
                sorted(
                    (item.obligation_id for item in requirements),
                    key=utf16_sort_key,
                )
            ),
        )
        for witness_id, requirements in sorted(
            required_by.items(),
            key=lambda item: utf16_sort_key(item[0]),
        )
    )
