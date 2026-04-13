"""build_kb.py — Chunk, embed, and load nutrition markdown documents into ChromaDB.

Run this script once (or whenever your documents change) to populate the vector store:

    python -m knowledge_base.build_kb

Steps:
    1. Load all .md files from knowledge_base/documents/
    2. Parse YAML frontmatter metadata from each file
    3. Chunk each document by ## section headings
    4. Embed chunks via OpenAI text-embedding-3-small
    5. Persist to ChromaDB at the path set by CHROMA_PERSIST_DIR env var (default: ./chroma_db)

The script is idempotent — running it again clears and rebuilds the collection.
"""

from __future__ import annotations

import os
import re
import yaml
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from rag.vectorstore import get_chroma_client

load_dotenv()

DOCUMENTS_DIR: Path = Path(__file__).parent / "documents"
_IS_STREAMLIT_CLOUD = os.path.exists('/mount/src')
CHROMA_PERSIST_DIR = '/tmp/chroma_db' if _IS_STREAMLIT_CLOUD else os.path.join(
    os.path.dirname(__file__), '..', 'knowledge_base', 'data', 'chroma_db'
)
print(f"[build_kb] CHROMA_PERSIST_DIR = {CHROMA_PERSIST_DIR}")
COLLECTION_NAME: str = "nutrition_kb"
EMBEDDING_MODEL: str = "text-embedding-3-small"

# Fields expected in the YAML frontmatter of each .md document.
_METADATA_FIELDS: tuple[str, ...] = (
    "ingredient",
    "category",
    "e_number",
    "aliases",
    "risk_level",
    "eu_status",
    "allergen",
    "vegan",
)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract and parse the YAML frontmatter block from a markdown document.

    Expects the file to begin with a block delimited by '---' on its own line.
    Values are normalised to types that ChromaDB accepts (str, int, float, bool).
    Specifically:
    - list values (e.g. aliases) are joined into a comma-separated string
    - None values are replaced with an empty string

    Args:
        text: Full raw content of a markdown file.

    Returns:
        A tuple of (metadata_dict, body_text) where body_text is everything
        after the closing '---' delimiter.

    Raises:
        ValueError: If the file does not contain a valid YAML frontmatter block.
    """
    pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
    match = pattern.match(text)
    if not match:
        raise ValueError("No valid YAML frontmatter block found (expected '---' delimiters).")

    raw_yaml = match.group(1)
    parsed: dict = yaml.safe_load(raw_yaml) or {}

    metadata: dict = {}
    for field in _METADATA_FIELDS:
        value = parsed.get(field)
        if isinstance(value, list):
            metadata[field] = ", ".join(str(v) for v in value)
        elif value is None:
            metadata[field] = ""
        else:
            metadata[field] = value

    body = text[match.end():]
    return metadata, body


def chunk_by_section(body: str, metadata: dict, source_stem: str) -> list[Document]:
    """Split a markdown body into one Document per '## ' section.

    Each chunk contains the section heading and its full content block.
    The shared metadata (from YAML frontmatter) and the source filename stem
    are attached to every chunk so they travel with the chunk during retrieval.

    Args:
        body: Markdown text after the frontmatter block.
        metadata: Parsed YAML fields to attach to every chunk.
        source_stem: Filename stem (e.g. 'BHA_BHT') used as the 'source' field.

    Returns:
        List of LangChain Document objects, one per section.
    """
    # Split on lines that start with '## ', keeping the delimiter in each chunk.
    raw_chunks = re.split(r"(?=^## )", body, flags=re.MULTILINE)

    documents: list[Document] = []

    for raw in raw_chunks:
        content = raw.strip()
        if not content:
            continue
        # Extract the ## heading as a separate metadata field so the UI can
        # display section names in the RAG process expander without parsing
        # page_content at query time.
        section_match = re.match(r"^##\s+(.+)", content)
        section_name = section_match.group(1).strip() if section_match else "Introduction"
        chunk_metadata = {**metadata, "source": source_stem, "section": section_name}
        documents.append(Document(page_content=content, metadata=chunk_metadata))

    return documents


def load_md_files(documents_dir: Path) -> tuple[list[Document], int]:
    """Load and chunk all .md files from the documents directory.

    Each file is parsed for YAML frontmatter, then split into per-section chunks.
    Files without a valid frontmatter block are skipped with a warning.

    Args:
        documents_dir: Path to the directory containing .md files.

    Returns:
        A tuple of (all_documents, file_count) where all_documents is the flat
        list of Document chunks and file_count is the number of files processed.
    """
    md_files = sorted(documents_dir.glob("*.md"))
    all_documents: list[Document] = []
    file_count = 0

    for path in md_files:
        text = path.read_text(encoding="utf-8")
        try:
            metadata, body = parse_frontmatter(text)
        except ValueError as exc:
            print(f"  [SKIP] {path.name}: {exc}")
            continue

        chunks = chunk_by_section(body, metadata, source_stem=path.stem)
        print(f"  {path.name}: {len(chunks)} chunks")
        all_documents.extend(chunks)
        file_count += 1

    return all_documents, file_count


def build_chroma(docs: list[Document]) -> None:
    """Embed documents and load them into the shared ChromaDB client.

    Uses the singleton client from rag.vectorstore — EphemeralClient on
    Streamlit Cloud, PersistentClient locally. Drops and recreates the
    collection each run so the script stays idempotent.

    Args:
        docs: List of Document chunks to embed and store.

    Raises:
        RuntimeError: If docs is empty (nothing to embed).
    """
    if not docs:
        raise RuntimeError("No documents to embed. Check that documents/ contains valid .md files.")

    client = get_chroma_client()
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass  # collection does not exist yet — fine

    embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)

    Chroma.from_documents(
        documents=docs,
        embedding=embeddings,
        collection_name=COLLECTION_NAME,
        client=client,
    )


def main() -> None:
    """Entry point — orchestrate the full knowledge base build."""
    destination = "[in-memory / EphemeralClient]" if _IS_STREAMLIT_CLOUD else str(Path(CHROMA_PERSIST_DIR).resolve())
    print("Building knowledge base...")
    print(f"  Source: {DOCUMENTS_DIR}")
    print(f"  Destination: {destination}")
    print()

    docs, file_count = load_md_files(DOCUMENTS_DIR)

    print()
    print(f"Embedding {len(docs)} chunks with {EMBEDDING_MODEL}...")
    build_chroma(docs)

    print()
    print("Done.")
    print(f"  Files loaded:   {file_count}")
    print(f"  Chunks created: {len(docs)}")
    print(f"  ChromaDB: {destination}")


if __name__ == "__main__":
    main()
