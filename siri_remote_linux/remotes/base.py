"""Contrato común para perfiles de mandos."""
from abc import ABC, abstractmethod


class RemoteProfile(ABC):
    model = ""

    @classmethod
    @abstractmethod
    def matches(cls, properties): ...

    @classmethod
    @abstractmethod
    def stable_identity(cls, properties): ...

    @abstractmethod
    def configure(self, characteristics, objects, read_descriptor): ...

    @abstractmethod
    def decode(self, path, value): ...

    @abstractmethod
    def reset(self): ...
