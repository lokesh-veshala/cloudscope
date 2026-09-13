from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Mapping


class CloudProvider(ABC):
    @abstractmethod
    def validate_connection(self) -> Mapping[str, object]: ...

    @abstractmethod
    def discover_resources(self) -> Iterable[Mapping[str, object]]: ...

    @abstractmethod
    def collect_usage(self) -> Iterable[Mapping[str, object]]: ...

    @abstractmethod
    def refresh_pricing(self) -> Mapping[str, object]: ...

