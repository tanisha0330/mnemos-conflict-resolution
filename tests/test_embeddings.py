import io
import json
from unittest.mock import MagicMock

import pytest

from src.ingestion.embeddings import EMBEDDING_DIM, generate_embedding


def _body(payload: dict):
    stream = MagicMock()
    stream.read.return_value = json.dumps(payload).encode()
    return stream


def test_generate_embedding_returns_vector():
    client = MagicMock()
    client.invoke_model.return_value = {"body": _body({"embedding": [0.1] * EMBEDDING_DIM})}
    result = generate_embedding("some claim", client=client, model_id="model-x")
    assert len(result) == EMBEDDING_DIM
    client.invoke_model.assert_called_once()


def test_generate_embedding_wrong_dimension_raises():
    client = MagicMock()
    client.invoke_model.return_value = {"body": _body({"embedding": [0.1] * 512})}
    with pytest.raises(ValueError):
        generate_embedding("some claim", client=client, model_id="model-x")
