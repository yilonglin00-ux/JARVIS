"""JarvisCore — der Orchestrator.

Der Core nimmt eine fertige Nutzeräußerung entgegen und liefert eine
Antwort als Textstrom. Er weiß nichts über Audio, nichts über WebSockets
und nichts über Anbieter — nur über `LLMProvider`, `Session` und den
Event-Bus. Alles, was in späteren Phasen dazukommt (Planner, Tools,
Agenten, Memory), hängt sich hier ein, ohne dass die Ränder sich ändern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog

from jarvis.config.settings import Settings
from jarvis.core.events import EventBus, ReplyDelta
from jarvis.core.session import Session
from jarvis.llm.base import LLMProvider, LLMRequest, TaskClass
from jarvis.llm.router import ModelRouter

log = structlog.get_logger(__name__)

# Gilt für jede Antwort, unabhängig vom Profil. Steht getrennt von der
# Persona, damit die Persona frei editierbar bleibt, ohne die harten
# Ausgabebedingungen der Sprachausgabe zu gefährden.
VOICE_OUTPUT_RULES = """
Deine Antworten werden von einer Sprachsynthese vorgelesen. Deshalb gilt
ausnahmslos: keine Markdown-Formatierung, keine Aufzählungszeichen, keine
Überschriften, keine Emojis, keine URLs im Fließtext. Schreibe Zahlen,
Abkürzungen und Einheiten so, wie man sie ausspricht. Halte dich kurz.
""".strip()


class JarvisCore:
    def __init__(
        self,
        *,
        llm: LLMProvider,
        settings: Settings,
        bus: EventBus,
        router: ModelRouter | None = None,
    ) -> None:
        self._llm = llm
        self._settings = settings
        self._bus = bus
        self._router = router or ModelRouter()

    @property
    def system_prompt(self) -> str:
        """Stabil über die gesamte Laufzeit — genau deshalb cachebar."""
        persona = self._settings.persona
        return f"{persona}\n\n{VOICE_OUTPUT_RULES}" if persona else VOICE_OUTPUT_RULES

    async def stream_reply(
        self,
        session: Session,
        user_text: str,
        *,
        task: TaskClass = TaskClass.CONVERSATION,
    ) -> AsyncIterator[str]:
        """Antwort auf `user_text` tokenweise liefern.

        Jedes Token wird zusätzlich als `ReplyDelta` veröffentlicht, damit
        die Sprechblase im Client mitschreiben kann, während die Sprach-
        ausgabe noch läuft. Der Verlauf wird hier *nicht* geschrieben —
        das macht die Session am Turn-Ende, weil erst dann feststeht, was
        tatsächlich erklungen ist.
        """
        request = LLMRequest(
            messages=session.messages_for(user_text),
            system=self.system_prompt,
            model=self._router.model_for(task),
            max_output_tokens=self._router.max_output_tokens,
            cache_system=self._llm.capabilities.prompt_caching,
        )
        log.debug(
            "llm.request",
            model=request.model,
            messages=len(request.messages),
            task=task.value,
        )

        async for chunk in self._llm.stream(request):
            if chunk.text:
                self._bus.publish(ReplyDelta(text=chunk.text))
                yield chunk.text
            if chunk.usage is not None:
                log.info(
                    "llm.usage",
                    model=request.model,
                    input_tokens=chunk.usage.input_tokens,
                    output_tokens=chunk.usage.output_tokens,
                    cached_input_tokens=chunk.usage.cached_input_tokens,
                )

    async def aclose(self) -> None:
        await self._llm.aclose()
