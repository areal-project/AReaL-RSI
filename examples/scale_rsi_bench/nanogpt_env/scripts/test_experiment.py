"""Packing contract checks, including comparison against the frozen verifier."""
import io
import json
from pathlib import Path
import random
import runpy
import unittest
from unittest.mock import patch

import numpy as np

from bench import validate_selection
from run_experiment import pack


class PackingTests(unittest.TestCase):
    def test_order_eos_and_partial_last_document(self):
        tokens = np.array([10, 11, 50256, 20, 21, 22, 50256], dtype=np.uint16)
        meta = np.array([[0, 2, 0], [3, 3, 0]], dtype=np.float64)
        actual, used = pack([1, 0], tokens, meta, budget=6)
        np.testing.assert_array_equal(actual, [20, 21, 22, 50256, 10, 11])
        self.assertEqual(used, 2)

    def test_invalid_selection(self):
        for ids in ([], [True], [1, 1], [-1], [182016], [1.5], {"ids": [1]}):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                validate_selection(ids)

    def test_insufficient_tokens(self):
        with self.assertRaisesRegex(ValueError, "requires"):
            pack([0], np.array([3, 50256], dtype=np.uint16), np.array([[0, 1, 0]]), budget=3)

    def test_matches_official_verifier_on_real_pool(self):
        from env_paths import runtime_root
        root = runtime_root(__file__)
        tokens = np.load(root / "pool/pool_tokens.npy", mmap_mode="r")
        meta = np.load(root / "pool/pool_meta.npy", mmap_mode="r")
        selection = list(range(len(meta)))
        random.Random(1337).shuffle(selection)
        actual, _ = pack(selection, tokens, meta)
        captured = {}

        def load_asset(path):
            return {"/tests/pool_tokens.npy": tokens, "/tests/pool_meta.npy": meta}[path]

        def capture(path, value):
            captured[path] = value.copy()

        # Execute the unchanged official packer, replacing only filesystem I/O.
        with patch("numpy.load", side_effect=load_asset), patch("numpy.save", side_effect=capture), \
             patch("builtins.open", return_value=io.StringIO(json.dumps(selection))):
            runpy.run_path(str(root / "source/tasks/nano-gpt-data-curation/tests/build_train_from_selection.py"))
        np.testing.assert_array_equal(actual, captured["/tmp/train.npy"])


if __name__ == "__main__":
    unittest.main()
