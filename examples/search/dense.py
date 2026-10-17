"""Frozen Qwen3 embedding retrieval for the disjoint external search corpora."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from examples.search.data import Passage, digest
from examples.search.reward import answer_in_text, normalize_answer

QUERY_PREFIX = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
)
SEARCH_DESCRIPTION = (
    "Perform a search on a knowledge source. Returns top-5 hits with docid, score, and snippet. "
    "The snippet contains the document's contents (may be truncated based on token limits)."
)


def read_corpus(path: Path) -> tuple[Passage, ...]:
    result = tuple(Passage(**json.loads(line)) for line in path.read_text().splitlines() if line)
    if not result or len({passage.docid for passage in result}) != len(result):
        raise ValueError("A search corpus must contain unique nonempty documents")
    return result


class DenseEncoder:
    def __init__(self, model_path: str):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.model = (
            AutoModel.from_pretrained(
                model_path,
                torch_dtype=torch.float16,
                attn_implementation="sdpa",
                local_files_only=True,
            )
            .to("cuda")
            .eval()
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path, padding_side="left", local_files_only=True
        )

    def encode(self, texts: list[str], *, query: bool) -> Any:
        import torch

        if query:
            texts = [QUERY_PREFIX + text for text in texts]
        tokens = self.tokenizer(
            texts, padding=True, truncation=True, max_length=8192, return_tensors="pt"
        ).to("cuda")
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16):
            output = self.model(**tokens).last_hidden_state[:, -1]
            output = torch.nn.functional.normalize(output, p=2, dim=1)
        return output.float().cpu().numpy()


def build(dataset: Path, output: Path, model: str, batch_size: int, snippet_tokenizer: str) -> None:
    import numpy as np
    from transformers import AutoTokenizer

    if output.exists():
        raise FileExistsError(f"Index output is immutable: {output}")
    encoder = DenseEncoder(model)
    tokenizer = AutoTokenizer.from_pretrained(snippet_tokenizer, local_files_only=True)
    output.mkdir(parents=True)
    metadata = {"model": model, "query_prefix": QUERY_PREFIX, "splits": {}}
    for split in ("train", "dev", "test"):
        source = dataset / f"{split}-corpus.jsonl"
        documents = read_corpus(source)
        blocks = []
        for offset in range(0, len(documents), batch_size):
            blocks.append(
                encoder.encode(
                    [document.text for document in documents[offset : offset + batch_size]],
                    query=False,
                )
            )
            if offset % (batch_size * 20) == 0:
                print(
                    json.dumps({"split": split, "encoded": offset, "total": len(documents)}),
                    flush=True,
                )
        matrix = np.concatenate(blocks)
        if not np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=0.003):
            raise ValueError("Document embeddings must be normalized")
        path = output / f"{split}.npy"
        np.save(path, matrix, allow_pickle=False)
        snippets = {}
        for document in documents:
            tokens = tokenizer.encode(document.text, add_special_tokens=False)
            snippets[document.docid] = normalize_answer(
                tokenizer.decode(tokens[:512], skip_special_tokens=True)
                if len(tokens) > 512
                else document.text
            )
        questions = [
            json.loads(line) for line in (dataset / f"{split}.jsonl").read_text().splitlines()
        ]
        for question in questions:
            if not any(
                answer_in_text(answer, snippets[docid])
                for answer in question["answers"]
                for docid in question["evidence_docids"]
            ):
                raise ValueError(
                    f"Gold answer is unavailable in tool snippets: {question['question_id']}"
                )
        retrieval = []
        if split != "test":
            for offset in range(0, len(questions), batch_size):
                batch = questions[offset : offset + batch_size]
                queries = encoder.encode([question["question"] for question in batch], query=True)
                scores = queries @ matrix.T
                for question, score in zip(batch, scores, strict=True):
                    ids = [
                        documents[index].docid for index in np.argsort(-score, kind="stable")[:5]
                    ]
                    evidence = set(question["evidence_docids"])
                    retrieval.append(
                        {
                            "question_id": question["question_id"],
                            "docids": ids,
                            "evidence_recall_at_5": len(evidence & set(ids)) / len(evidence),
                            "answer_visible_at_5": any(
                                answer_in_text(answer, snippets[docid])
                                for answer in question["answers"]
                                for docid in ids
                            ),
                        }
                    )
            (output / f"{split}-preflight.json").write_text(json.dumps(retrieval, indent=2) + "\n")
        metadata["splits"][split] = {
            "documents": len(documents),
            "dimension": matrix.shape[1],
            "corpus_sha256": digest(source),
            "index_sha256": digest(path),
            "gold_answers_visible_in_snippets": True,
            "raw_query_evidence_recall_at_5": sum(row["evidence_recall_at_5"] for row in retrieval)
            / len(retrieval)
            if retrieval
            else None,
            "raw_query_answer_visibility_at_5": sum(row["answer_visible_at_5"] for row in retrieval)
            / len(retrieval)
            if retrieval
            else None,
        }
    (output / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")


class DenseSearch:
    def __init__(self, corpus: Path, index: Path, model: str, snippet_tokenizer: str):
        import numpy as np
        from transformers import AutoTokenizer

        self.documents = read_corpus(corpus)
        self.matrix = np.load(index, allow_pickle=False)
        manifest = json.loads((index.parent / "manifest.json").read_text())
        expected = manifest["splits"][index.stem]
        if expected["corpus_sha256"] != digest(corpus) or expected["index_sha256"] != digest(index):
            raise ValueError("Index and corpus do not match the frozen manifest")
        if len(self.documents) != self.matrix.shape[0] or manifest["model"] != model:
            raise ValueError("Retrieval model or document count does not match the index")
        self.encoder = DenseEncoder(model)
        tokenizer = AutoTokenizer.from_pretrained(snippet_tokenizer, local_files_only=True)
        self.snippets = []
        for document in self.documents:
            tokens = tokenizer.encode(document.text, add_special_tokens=False)
            self.snippets.append(
                tokenizer.decode(tokens[:512], skip_special_tokens=True)
                if len(tokens) > 512
                else document.text
            )
        self.lock = threading.Lock()

    def search_description(self, k: int = 5) -> str:
        if k != 5:
            raise ValueError("The frozen search interface returns top five documents")
        return SEARCH_DESCRIPTION

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        import numpy as np

        with self.lock:
            vector = self.encoder.encode([query], query=True)[0]
            scores = self.matrix @ vector
            ranks = np.argsort(-scores, kind="stable")[:k]
        return [
            {
                "docid": self.documents[index].docid,
                "score": float(scores[index]),
                "text": self.documents[index].text,
            }
            for index in ranks
        ]


def serve(
    corpus: Path, index: Path, model: str, tokenizer: str, port: int, benchmark: Path
) -> None:
    from fastmcp import FastMCP

    sys.path.insert(0, str(benchmark / "searcher"))
    from tools import register_tools

    searcher = DenseSearch(corpus, index, model, tokenizer)
    os.environ["BCP_SNIPPET_TOKENIZER"] = tokenizer
    mcp = FastMCP(name="search-server")
    register_tools(mcp, searcher, snippet_max_tokens=512, k=5, include_get_document=False)

    print(
        json.dumps(
            {
                "status": "ready",
                "corpus": str(corpus),
                "index": str(index),
                "documents": len(searcher.documents),
                "port": port,
            }
        ),
        flush=True,
    )
    mcp.run(transport="sse", path="/mcp", port=port)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("build", "serve"))
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--tokenizer")
    parser.add_argument("--port", type=int)
    parser.add_argument("--benchmark-repository", type=Path)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if args.mode == "build":
        if args.dataset is None or args.output is None or args.tokenizer is None:
            parser.error("build requires --dataset, --output and --tokenizer")
        build(args.dataset, args.output, args.model, args.batch_size, args.tokenizer)
    else:
        if None in (args.corpus, args.index, args.tokenizer, args.port, args.benchmark_repository):
            parser.error(
                "serve requires --corpus, --index, --tokenizer, --port and --benchmark-repository"
            )
        serve(
            args.corpus,
            args.index,
            args.model,
            args.tokenizer,
            args.port,
            args.benchmark_repository,
        )


if __name__ == "__main__":
    main()
