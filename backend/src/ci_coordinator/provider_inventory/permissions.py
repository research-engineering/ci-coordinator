"""Deployment-profile authorization for control-plane inventory reads."""

from __future__ import annotations

from typing import Literal

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import is_administrator_actor_id
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_coordinator.provider_inventory.model import InventoryGrant, InventoryMode

_MAX_INSTALLATIONS = 64


class ControlPlaneProviderInventoryAuthorizer:
    def __init__(
        self,
        *,
        inventory_installation_ids: frozenset[int],
        workbench_scopes: frozenset[RepositoryScope],
        inventory_mode: InventoryMode = "restricted",
        scope_mode: Literal["restricted", "app"] = "restricted",
    ) -> None:
        if type(inventory_installation_ids) is not frozenset or (
            len(inventory_installation_ids) > _MAX_INSTALLATIONS
            or any(
                type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER
                for value in inventory_installation_ids
            )
        ):
            raise ValueError("provider inventory installation grant is invalid")
        if type(workbench_scopes) is not frozenset or any(
            type(scope) is not RepositoryScope for scope in workbench_scopes
        ):
            raise ValueError("provider inventory workbench scopes are invalid")
        self._grant = InventoryGrant(inventory_mode, tuple(sorted(inventory_installation_ids)))
        if scope_mode not in {"restricted", "app"} or (
            scope_mode == "app" and inventory_mode != "app"
        ):
            raise ValueError("App scope mode requires App inventory")
        self._scope_mode = scope_mode
        self._workbench_ids = {
            installation_id: frozenset(
                scope.repository_id
                for scope in workbench_scopes
                if scope.installation_id == installation_id
            )
            for installation_id in {scope.installation_id for scope in workbench_scopes}
        }

    def authorized_installations(self, actor: str) -> InventoryGrant | None:
        return self._grant if is_administrator_actor_id(actor) else None

    def permits_all_workbench_repositories(self, actor: str, installation_id: int) -> bool:
        return (
            is_administrator_actor_id(actor)
            and self._grant.permits(installation_id)
            and self._scope_mode == "app"
        )

    def workbench_repository_ids(
        self,
        actor: str,
        installation_id: int,
    ) -> frozenset[int] | None:
        if not is_administrator_actor_id(actor) or not self._grant.permits(installation_id):
            return None
        return self._workbench_ids.get(installation_id, frozenset())
