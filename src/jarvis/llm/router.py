"""Modellwahl nach Aufgabenklasse.

In Phase 2 ist das eine Tabelle. Der Router existiert trotzdem schon, weil
sonst später Modellnamen quer durch den Code wandern — und weil hier
das Kostenbudget andocken wird (Architektur §6, §16).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.llm.base import TaskClass

DEFAULT_MODELS: dict[TaskClass, str] = {
    TaskClass.CLASSIFICATION: "claude-haiku-4-5",
    TaskClass.CONVERSATION: "claude-sonnet-5",
    TaskClass.PLANNING: "claude-opus-5",
}


@dataclass(slots=True)
class ModelRouter:
    models: dict[TaskClass, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    max_output_tokens: int = 1024

    @classmethod
    def from_options(cls, options: dict[str, object]) -> ModelRouter:
        """Aus dem `llm.<provider>`-Block der Profilkonfiguration bauen."""
        configured = options.get("models")
        models = dict(DEFAULT_MODELS)
        if isinstance(configured, dict):
            for key, value in configured.items():
                if isinstance(value, str) and value:
                    try:
                        models[TaskClass(key)] = value
                    except ValueError:
                        # Unbekannte Aufgabenklasse in der Config ist kein
                        # Grund, den Start zu verweigern.
                        continue
        raw_tokens = options.get("max_output_tokens", 1024)
        tokens = raw_tokens if isinstance(raw_tokens, int) else 1024
        return cls(models=models, max_output_tokens=tokens)

    def model_for(self, task: TaskClass) -> str:
        return self.models.get(task, DEFAULT_MODELS[TaskClass.CONVERSATION])
