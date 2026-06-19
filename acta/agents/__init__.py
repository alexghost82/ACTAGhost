"""The twelve ACTA sub-agents plus specialized worker agents.

Each agent carries the exact sub-prompt from the ACTA specification in its
``SUB_PROMPT`` attribute and a single, well-defined responsibility.
"""

from acta.agents.base import AgentServices, BaseAgent
from acta.agents.context_agent import ContextAgent
from acta.agents.decision_agent import DecisionAgent
from acta.agents.integration_agent import IntegrationAgent
from acta.agents.intent_agent import IntentAgent
from acta.agents.knowledge_graph_agent import KnowledgeGraphAgent
from acta.agents.memory_agent import MemoryAgent
from acta.agents.multimodal_agent import MultimodalAgent
from acta.agents.planning_agent import PlanningAgent
from acta.agents.reasoning_agent import ReasoningAgent
from acta.agents.security_agent import SecurityAgent
from acta.agents.specialized import (
    AutomationAgent,
    CodingAgent,
    ResearchAgent,
    SystemAgent,
    WORKER_AGENTS,
)
from acta.agents.ui_agent import UIAgent

__all__ = [
    "AgentServices",
    "BaseAgent",
    "IntentAgent",
    "ContextAgent",
    "ReasoningAgent",
    "PlanningAgent",
    "DecisionAgent",
    "MemoryAgent",
    "KnowledgeGraphAgent",
    "IntegrationAgent",
    "SecurityAgent",
    "MultimodalAgent",
    "UIAgent",
    "ResearchAgent",
    "AutomationAgent",
    "CodingAgent",
    "SystemAgent",
    "WORKER_AGENTS",
]
