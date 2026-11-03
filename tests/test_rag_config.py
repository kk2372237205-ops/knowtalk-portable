# -*- coding: utf-8 -*-
"""rag_service.config.build_runtime_config 的结构与取值正确性。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rag_service.config import build_runtime_config, CHUNK_SIZE, CHUNK_OVERLAP, VECTOR_TOP_K, BM25_TOP_K, VECTOR_THRESHOLD, RERANK_TOP_K


class RagConfigTest(unittest.TestCase):
    def setUp(self):
        self.cfg = build_runtime_config()

    def test_top_level_groups_present(self):
        for key in ("vector_store", "embedding", "llm", "chunking", "reranker", "retrieval", "source"):
            self.assertIn(key, self.cfg, key)

    def test_vector_store_is_hybrid(self):
        vs = self.cfg["vector_store"]
        self.assertIn("chromadb", vs["type"])
        self.assertIn("bm25", vs["type"])
        self.assertEqual(vs["distance_metric"], "cosine")
        self.assertEqual(vs["hnsw"]["M"], 32)
        self.assertEqual(vs["hnsw"]["construction_ef"], 200)

    def test_chunking_values_match_constants(self):
        ck = self.cfg["chunking"]
        self.assertEqual(ck["chunk_size"], CHUNK_SIZE)
        self.assertEqual(ck["chunk_overlap"], CHUNK_OVERLAP)
        self.assertTrue(ck["chunk_size"] > 0)
        self.assertTrue(ck["chunk_overlap"] >= 0)

    def test_reranker_and_retrieval_match_constants(self):
        self.assertEqual(self.cfg["reranker"]["keep"], RERANK_TOP_K)
        rt = self.cfg["retrieval"]
        self.assertEqual(rt["vector_top_k"], VECTOR_TOP_K)
        self.assertEqual(rt["bm25_top_k"], BM25_TOP_K)
        self.assertEqual(rt["vector_threshold"], VECTOR_THRESHOLD)

    def test_source_accepts_pdf(self):
        src = self.cfg["source"]
        self.assertIn(".pdf", src["accepted"])


if __name__ == "__main__":
    unittest.main()
