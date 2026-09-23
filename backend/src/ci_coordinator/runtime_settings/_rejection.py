from ci_coordinator.runtime_settings.contracts import RuntimeSettingsRejection


class SettingsRejected(Exception):
    def __init__(self, rejection: RuntimeSettingsRejection) -> None:
        super().__init__(rejection.code, rejection.field_name)
        self.rejection = rejection
