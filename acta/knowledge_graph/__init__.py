"""Knowledge Graph: entities and relations for intelligent search and analysis.

The MVP uses an in-process directed graph (networkx) persisted to JSON. The
interface mirrors what a Neo4j-backed implementation would expose, so the
backend can be swapped without touching the agents.
"""

from acta.knowledge_graph.graph import KnowledgeGraph

__all__ = ["KnowledgeGraph"]
