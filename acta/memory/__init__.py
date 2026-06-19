"""Memory System: episodic, semantic, personal and procedural memory.

Records are stored in a local SQLite database with their content encrypted at
rest. Retrieval uses a lightweight lexical relevance score so the MVP needs no
external vector database, while the interface is ready to be backed by
PostgreSQL + pgvector in production.
"""

from acta.memory.store import MemoryRecord, MemoryStore

__all__ = ["MemoryRecord", "MemoryStore"]
