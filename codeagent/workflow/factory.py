"""Workflow factory for building compiled LangGraph graphs."""

from __future__ import annotations

from collections.abc import Mapping

from codeagent.workflow.main_graph import StageHandler, build_main_graph
from codeagent.workflow.routing import StageRouter


class WorkflowFactory:
    def __init__(
        self,
        *,
        stage_handlers: Mapping[str, StageHandler] | None = None,
        router: StageRouter | None = None,
    ) -> None:
        self.stage_handlers = stage_handlers or {}
        self.router = router or StageRouter()

    def build(self, *, checkpointer=None):
        return build_main_graph(
            stage_handlers=self.stage_handlers,
            router=self.router,
            checkpointer=checkpointer,
        )
