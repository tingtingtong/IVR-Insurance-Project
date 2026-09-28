import asyncio
from langchain_community.vectorstores import PGVector
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
from config import settings

COLLECTION_NAME = "insuranceCompany_knowledge"

_vector_store: PGVector | None = None


def _connection_string() -> str:
    url = settings.database_url
    if settings.is_prod and "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = url + sep + "sslmode=require"
    return url


def _get_store() -> PGVector:
    global _vector_store
    if _vector_store is None:
        embeddings = OpenAIEmbeddings(
            model=settings.openai_embedding_model,
            openai_api_key=settings.openai_api_key,
        )
        _vector_store = PGVector(
            collection_name=COLLECTION_NAME,
            connection_string=_connection_string(),
            embedding_function=embeddings,
        )
    return _vector_store


def warm_store() -> None:
    """Pre-init embeddings + pgvector so the first FAQ caller is not a cold start."""
    try:
        _get_store()
    except Exception:
        pass


async def search_knowledge(query: str, k: int = 3) -> str:
    """
    Retrieve top-k relevant chunks from the insuranceCompany knowledge base.
    Returns concatenated context string for LLM grounding.
    Returns empty string if DB is unavailable (FAQ node falls back to LLM only).
    """
    global _vector_store
    try:
        store = _get_store()
        docs: list[Document] = await asyncio.to_thread(store.similarity_search, query, k)
        if not docs:
            return ""
        return "\n\n".join(d.page_content for d in docs)
    except Exception:
        _vector_store = None
        return ""


async def ingest_documents(documents: list[Document]) -> None:
    """Ingest documents into the vector store (run once / on update)."""
    store = _get_store()
    store.add_documents(documents)
