"""
AI Scout semantic search via TF-IDF + cosine similarity.

Builds a text index over project summaries + solves labels.
Used by the recommend() MCP tool for semantic matching.
Pure local computation — no external API needed.

Usage:
  python -m ai_scout.hermes.embed index    # Build/rebuild TF-IDF index
  python -m ai_scout.hermes.embed query "web scraping"  # Test search
  python -m ai_scout.hermes.embed status   # Show index stats
"""

import argparse
import json
import os
import pickle
import sqlite3
import sys
from pathlib import Path

# Resolve project root: src/ai_scout/hermes/ -> project root is parents[3]
# (src/ai_scout/hermes/embed.py -> parents[0]=hermes, [1]=ai_scout, [2]=src, [3]=project_root)
_PROJECT_ROOT = Path(__file__).parents[3]
DB_PATH = Path(os.environ.get(
    "AI_SCOUT_DB",
    str(_PROJECT_ROOT / "data" / "ai_scout.db")
))
INDEX_PATH = Path(os.environ.get(
    "AI_SCOUT_INDEX",
    str(_PROJECT_ROOT / "data" / "tfidf_index.pkl")
))


def _get_texts(con) -> tuple[list[int], list[str]]:
    """Extract project IDs and their searchable text from DB."""
    rows = con.execute(
        """SELECT p.id, p.full_name, p.summary, p.solves, p.description,
                  p.product_type, p.integration_shape, p.language
           FROM projects p
           WHERE p.is_active = 1
             AND p.summary IS NOT NULL AND p.summary != ''
           ORDER BY p.id"""
    ).fetchall()

    ids = []
    texts = []
    for row in rows:
        ids.append(row["id"])
        parts = [
            row["summary"] or "",
            row["description"] or "",
            row["product_type"] or "",
            row["integration_shape"] or "",
            row["language"] or "",
        ]
        # Parse solves
        solves = row["solves"] or "[]"
        if isinstance(solves, str):
            try:
                solves = json.loads(solves)
            except Exception:
                solves = []
        if isinstance(solves, list):
            parts.extend(solves)

        texts.append(" ".join(str(p) for p in parts if p))

    return ids, texts


def build_index(con):
    """Build or rebuild the TF-IDF index."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np

    ids, texts = _get_texts(con)
    if not texts:
        print("No enriched projects to index.")
        return

    print(f"Building TF-IDF index for {len(texts)} projects...")

    vectorizer = TfidfVectorizer(
        max_features=5000,
        stop_words="english",
        ngram_range=(1, 2),
        sublinear_tf=True,
    )
    tfidf_matrix = vectorizer.fit_transform(texts)

    # Save index
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "wb") as f:
        pickle.dump({
            "ids": ids,
            "vectorizer": vectorizer,
            "matrix": tfidf_matrix,
            "count": len(ids),
        }, f)

    print(f"Done. Indexed {len(ids)} projects. Matrix shape: {tfidf_matrix.shape}")
    print(f"Index saved to: {INDEX_PATH}")


def search(query: str, limit: int = 5, con=None) -> list[dict]:
    """Search for projects matching a query string."""
    from sklearn.metrics.pairwise import cosine_similarity

    if not INDEX_PATH.exists():
        return []

    with open(INDEX_PATH, "rb") as f:
        index = pickle.load(f)

    vectorizer = index["vectorizer"]
    matrix = index["matrix"]
    ids = index["ids"]

    # Transform query
    query_vec = vectorizer.transform([query])
    scores = cosine_similarity(query_vec, matrix).flatten()

    # Get top results
    import numpy as np
    top_indices = np.argsort(scores)[::-1][:limit]

    if con is None:
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        should_close = True
    else:
        should_close = False

    results = []
    for idx in top_indices:
        if scores[idx] < 0.01:  # Skip very low similarity
            continue
        project_id = ids[idx]
        row = con.execute(
            """SELECT p.id, p.full_name, p.product_type, p.summary, p.solves,
                      p.compatible_with, p.install, p.integration_shape,
                      p.llm_quality_score
               FROM projects p WHERE p.id = ?""",
            (project_id,)
        ).fetchone()
        if row:
            r = dict(row)
            for field in ("solves", "compatible_with", "install"):
                v = r.get(field)
                if isinstance(v, str):
                    try:
                        r[field] = json.loads(v)
                    except Exception:
                        pass
            r["similarity_score"] = float(scores[idx])
            results.append(r)

    if should_close:
        con.close()

    return results


def show_status():
    """Show index status."""
    if not INDEX_PATH.exists():
        print("Index not built yet. Run: python -m ai_scout.hermes.embed index")
        return

    with open(INDEX_PATH, "rb") as f:
        index = pickle.load(f)

    print(f"TF-IDF Index:")
    print(f"  Projects: {index['count']}")
    print(f"  Matrix shape: {index['matrix'].shape}")
    print(f"  Path: {INDEX_PATH}")


def main():
    parser = argparse.ArgumentParser(description="AI Scout TF-IDF index")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("index", help="Build/rebuild TF-IDF index")
    sub.add_parser("status", help="Show index stats")

    p_query = sub.add_parser("query", help="Semantic search")
    p_query.add_argument("query", help="Search query")
    p_query.add_argument("--limit", type=int, default=5)

    args = parser.parse_args()

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row

    if args.cmd == "index":
        build_index(con)
    elif args.cmd == "status":
        show_status()
    elif args.cmd == "query":
        results = search(args.query, limit=args.limit, con=con)
        print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    else:
        parser.print_help()

    con.close()


if __name__ == "__main__":
    main()
