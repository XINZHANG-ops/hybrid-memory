from loguru import logger
import ollama
import numpy as np


class EmbeddingClient:
    def __init__(self, model: str = "embeddinggemma:300m", base_url: str = "http://localhost:11434"):
        self.model = model
        self.client = ollama.Client(host=base_url)
        self._dimension = None
        logger.info(f"EmbeddingClient initialized: model={model}, base_url={base_url}")

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            test_embedding = self.embed("test")
            self._dimension = len(test_embedding)
            logger.info(f"Embedding dimension: {self._dimension}")
        return self._dimension

    def embed(self, text: str) -> np.ndarray:
        try:
            response = self.client.embed(model=self.model, input=text)
            embedding = response.get("embeddings", [[]])[0]
            return np.array(embedding, dtype=np.float32)
        except Exception as e:
            logger.error(f"Embedding error: {e}")
            raise

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        try:
            response = self.client.embed(model=self.model, input=texts)
            embeddings = response.get("embeddings", [])
            return np.array(embeddings, dtype=np.float32)
        except Exception as e:
            logger.error(f"Batch embedding error: {e}")
            raise
