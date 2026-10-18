"""Optional backend-runtime tests: actual HF loading must preserve the saved policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from areal_rsi.infra.training import canonicalize_tied_checkpoint

torch = pytest.importorskip("torch", reason="Run in the AReaL backend environment")
safetensors = pytest.importorskip("safetensors.torch")
transformers = pytest.importorskip("transformers")


def test_tied_checkpoint_round_trip_preserves_logits_and_parameter_sharing(tmp_path: Path) -> None:
    torch.manual_seed(17)
    config = transformers.Qwen2Config(
        vocab_size=32,
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=1,
        num_attention_heads=2,
        num_key_value_heads=2,
        tie_word_embeddings=True,
    )
    model = transformers.Qwen2ForCausalLM(config).eval()
    assert model.get_input_embeddings().weight is model.get_output_embeddings().weight
    config.save_pretrained(tmp_path)
    # Mimic the distributed export that serializes both names independently.
    tensors = {name: tensor.detach().clone() for name, tensor in model.state_dict().items()}
    safetensors.save_file(tensors, str(tmp_path / "model.safetensors"), metadata={"format": "pt"})
    assert canonicalize_tied_checkpoint(tmp_path)
    loaded = transformers.AutoModelForCausalLM.from_pretrained(tmp_path).eval()
    assert loaded.get_input_embeddings().weight is loaded.get_output_embeddings().weight
    tokens = torch.tensor([[1, 2, 3, 4]])
    with torch.inference_mode():
        torch.testing.assert_close(model(tokens).logits, loaded(tokens).logits)
    assert not canonicalize_tied_checkpoint(tmp_path)


def test_tied_declaration_with_different_matrices_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"tie_word_embeddings": True}))
    path = tmp_path / "model.safetensors"
    safetensors.save_file(
        {
            "model.embed_tokens.weight": torch.zeros(4, 8),
            "lm_head.weight": torch.ones(4, 8),
        },
        str(path),
        metadata={"format": "pt"},
    )
    original = path.read_bytes()
    with pytest.raises(ValueError, match="different input/output weights"):
        canonicalize_tied_checkpoint(tmp_path)
    assert path.read_bytes() == original
    assert json.loads(config.read_text())["tie_word_embeddings"] is True
