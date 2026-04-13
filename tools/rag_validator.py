"""RAG-based meal safety validator using the Sprint 2 ChromaDB knowledge base.

Queries the nutrition knowledge base to validate whether a meal contains
ingredients with known safety concerns (additives, allergens, risk factors).
Uses query translation to generate multiple search phrasings for better recall.
"""

import os
import hashlib
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from prompts.system_prompts import RAG_QUERY_TRANSLATION_PROMPT

load_dotenv()

# ---------------------------------------------------------------------------
# Module-level ChromaDB initialisation (runs once at import, not per call)
# ---------------------------------------------------------------------------

_COLLECTION_NAME = "nutrition_kb"
_EMBEDDING_MODEL = "text-embedding-3-small"

_IS_STREAMLIT_CLOUD = os.path.exists("/mount/src")
_CHROMA_PERSIST_DIR = (
    "/tmp/chroma_db"
    if _IS_STREAMLIT_CLOUD
    else str(Path(__file__).parent.parent / "knowledge_base" / "data" / "chroma_db")
)

_collection = None  # chromadb.Collection — set below if init succeeds
_embeddings = None  # OpenAIEmbeddings instance

try:
    if not Path(_CHROMA_PERSIST_DIR).exists():
        raise FileNotFoundError(
            f"ChromaDB directory not found at {_CHROMA_PERSIST_DIR}. "
            "Run 'python -m knowledge_base.build_kb' to build the knowledge base first."
        )
    import chromadb

    _client = chromadb.PersistentClient(path=_CHROMA_PERSIST_DIR)
    _collection = _client.get_collection(name=_COLLECTION_NAME)
    _embeddings = OpenAIEmbeddings(model=_EMBEDDING_MODEL)
except Exception:
    # Graceful fallback — the tool will return a clear message at call time.
    _collection = None
    _embeddings = None


# ---------------------------------------------------------------------------
# Query translation
# ---------------------------------------------------------------------------


def _generate_queries(meal_description: str, user_context: str) -> list[str]:
    """Use an LLM to generate 2-3 alternative search queries for the KB.

    Combines the meal description and user context to produce diverse queries
    that cover specific ingredients, user-specific safety concerns, and
    broader category-level risks.

    Falls back to ``[meal_description]`` if the LLM call fails.
    """
    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
        prompt = RAG_QUERY_TRANSLATION_PROMPT.format(
            meal_description=meal_description,
            user_context=user_context or "No specific context provided",
        )
        response = llm.invoke(prompt)
        queries = [
            line.strip()
            for line in response.content.strip().splitlines()
            if line.strip()
        ]
        if len(queries) < 2:
            return [meal_description]
        return queries[:3]
    except Exception:
        return [meal_description]


# ---------------------------------------------------------------------------
# Deduplication & formatting helpers
# ---------------------------------------------------------------------------


def _deduplicate_results(
    documents: list[str],
    metadatas: list[dict],
    distances: list[float],
) -> list[tuple[str, dict, float]]:
    """Deduplicate retrieved chunks by content hash, keeping the best distance.

    ChromaDB IDs may change across collection rebuilds, so deduplication is
    based on a SHA-256 hash of the page content instead.
    """
    seen: dict[str, tuple[str, dict, float]] = {}
    for doc, meta, dist in zip(documents, metadatas, distances):
        content_hash = hashlib.sha256(doc.encode()).hexdigest()
        if content_hash not in seen or dist < seen[content_hash][2]:
            seen[content_hash] = (doc, meta, dist)
    return sorted(seen.values(), key=lambda x: x[2])


def _format_chunk(rank: int, content: str, metadata: dict) -> str:
    """Format a single retrieved chunk for the tool output."""
    ingredient = metadata.get("ingredient", "unknown")
    section = metadata.get("section", "unknown")
    risk_level = metadata.get("risk_level", "unknown")
    source = metadata.get("source", "unknown")
    allergen = metadata.get("allergen", False)

    header = (
        f"[{rank}] {ingredient} — {section} "
        f"(risk: {risk_level}, allergen: {'yes' if allergen else 'no'}, "
        f"source: {source})"
    )
    truncated = content[:500] + "..." if len(content) > 500 else content
    return f"{header}\n{truncated}"


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


@tool
def validate_meal_safety(meal_description: str, user_context: str) -> str:
    """
    Retrieve evidence from the nutrition knowledge base to validate dietary
    safety and nutritional claims about a meal. Uses query translation to
    improve retrieval quality by generating multiple search phrasings.

    The knowledge base covers:
    - Food additives (preservatives, sweeteners, emulsifiers, colourings)
    - Common allergens and allergen-containing ingredients
    - Oils and fats (palm oil, coconut oil, olive oil, etc.)
    - Processed food ingredients (refined flour, high-fructose corn syrup, etc.)

    Each result includes the ingredient name, risk level, allergen status, and
    a relevant text excerpt covering health risks, benefits, or allergen info.

    Always call this tool before finalising any meal suggestion to check for
    additive or ingredient safety concerns. It complements the allergen checker
    by providing deeper evidence-based safety context from a curated knowledge
    base.

    Args:
        meal_description: A description of the meal and its ingredients, e.g.
                          "grilled salmon with brown rice and spinach" or
                          "diet cola, white bread sandwich with processed cheese".
        user_context: The user's relevant health context as a plain string, e.g.
                      "allergies: tree nuts, soy; goals: weight loss;
                      conditions: diabetes". Pass "none" if no special context.

    Returns:
        A formatted string with the top 3 most relevant knowledge base entries,
        including ingredient name, risk level, allergen status, and an excerpt.
        If no relevant results are found, returns a message indicating the meal
        has no flagged concerns. If the knowledge base is unavailable, returns
        an explanatory fallback message.
    """
    if _collection is None or _embeddings is None:
        return (
            "RAG knowledge base is currently unavailable. "
            "The meal could not be validated against the ingredient safety database. "
            "Please verify ingredients manually or try again later."
        )

    # Step 1 — Query translation: generate diverse search queries
    queries = _generate_queries(meal_description, user_context)

    # Step 2 — Retrieve from ChromaDB with each reformulated query
    all_documents: list[str] = []
    all_metadatas: list[dict] = []
    all_distances: list[float] = []

    for query in queries:
        try:
            embedding = _embeddings.embed_query(query)
            results = _collection.query(
                query_embeddings=[embedding],
                n_results=3,
                include=["documents", "metadatas", "distances"],
            )
            if results["documents"] and results["documents"][0]:
                all_documents.extend(results["documents"][0])
                all_metadatas.extend(results["metadatas"][0])
                all_distances.extend(results["distances"][0])
        except Exception:
            continue  # skip failed queries, try the rest

    if not all_documents:
        return (
            "No relevant safety information found in the knowledge base for this meal. "
            "This does not guarantee the meal is safe — it means the knowledge base "
            "does not contain specific warnings about the described ingredients."
        )

    # Step 3 — Deduplicate and take top 3
    unique_results = _deduplicate_results(all_documents, all_metadatas, all_distances)
    top_results = unique_results[:3]

    # Step 4 — Format output
    chunks = []
    for i, (content, metadata, _distance) in enumerate(top_results, start=1):
        chunks.append(_format_chunk(i, content, metadata))

    header = f"Safety check results ({len(top_results)} relevant entries found):\n\n"
    # CAPSTONE: Add Open Food Facts product lookup for branded/packaged food validation
    return header + "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        {
            "meal_description": "Diet cola, white bread sandwich with processed cheese and ham",
            "user_context": "allergies: none; goals: weight loss",
        },
        {
            "meal_description": "Grilled salmon with olive oil, steamed broccoli, and brown rice",
            "user_context": "allergies: tree nuts; goals: heart health",
        },
        {
            "meal_description": "Protein shake with whey powder, almond milk, and a granola bar",
            "user_context": "allergies: dairy, gluten; conditions: PKU",
        },
        {
            "meal_description": "Fresh fruit salad with honey and Greek yogurt",
            "user_context": "none",
        },
    ]

    for t in tests:
        print(f"\n{'=' * 70}")
        print(f"Meal: {t['meal_description']}")
        print(f"Context: {t['user_context']}")
        print(f"{'=' * 70}")
        result = validate_meal_safety.invoke(t)
        print(result)
