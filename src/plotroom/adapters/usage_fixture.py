"""A deterministic project-local usage meter for tests and local fixtures."""

from plotroom.ports.usage_meter import UsageObservation


class FixtureUsageMeter:
    def __init__(self, readings: tuple[UsageObservation, ...] = ()) -> None:
        self.readings = readings

    async def observations(self) -> tuple[UsageObservation, ...]:
        return self.readings
