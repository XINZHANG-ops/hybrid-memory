import json
from pathlib import Path
from loguru import logger
import numpy as np
import faiss


class VectorStore:
    def __init__(self, db_path: Path, dimension: int = 768):
        self.db_path = Path(db_path)
        self.dimension = dimension
        self.index_path = self.db_path.parent / f"{self.db_path.stem}_vectors.faiss"
        self.mapping_path = self.db_path.parent / f"{self.db_path.stem}_vectors_mapping.json"

        self.index = None
        self.id_to_msg: dict[int, int] = {}  # faiss_id -> message_id
        self.msg_to_id: dict[int, int] = {}  # message_id -> faiss_id
        self.next_id = 0

        self._load_or_create()
        logger.info(f"VectorStore initialized: {self.index_path}, vectors={self.index.ntotal if self.index else 0}")

    def _load_or_create(self):
        if self.index_path.exists() and self.mapping_path.exists():
            try:
                self.index = faiss.read_index(str(self.index_path))
                with open(self.mapping_path, 'r') as f:
                    data = json.load(f)
                    self.id_to_msg = {int(k): v for k, v in data.get("id_to_msg", {}).items()}
                    self.msg_to_id = {int(k): v for k, v in data.get("msg_to_id", {}).items()}
                    self.next_id = data.get("next_id", 0)
                logger.info(f"Loaded existing vector index: {self.index.ntotal} vectors")
                return
            except Exception as e:
                logger.warning(f"Failed to load index, creating new: {e}")

        self.index = faiss.IndexFlatIP(self.dimension)  # Inner Product for cosine similarity
        self.id_to_msg = {}
        self.msg_to_id = {}
        self.next_id = 0
        logger.info(f"Created new vector index: dimension={self.dimension}")

    def save(self):
        try:
            faiss.write_index(self.index, str(self.index_path))
            with open(self.mapping_path, 'w') as f:
                json.dump({
                    "id_to_msg": self.id_to_msg,
                    "msg_to_id": self.msg_to_id,
                    "next_id": self.next_id
                }, f)
            logger.debug(f"Saved vector index: {self.index.ntotal} vectors")
        except Exception as e:
            logger.error(f"Failed to save index: {e}")

    def add(self, message_id: int, embedding: np.ndarray):
        if message_id in self.msg_to_id:
            logger.debug(f"Message {message_id} already indexed, skipping")
            return

        embedding = embedding.reshape(1, -1).astype(np.float32)
        faiss.normalize_L2(embedding)  # Normalize for cosine similarity

        self.index.add(embedding)
        faiss_id = self.next_id
        self.id_to_msg[faiss_id] = message_id
        self.msg_to_id[message_id] = faiss_id
        self.next_id += 1

        self.save()
        logger.debug(f"Added message {message_id} to vector index (faiss_id={faiss_id})")

    def search(self, query_embedding: np.ndarray, k: int = 10) -> list[tuple[int, float]]:
        if self.index.ntotal == 0:
            return []

        query = query_embedding.reshape(1, -1).astype(np.float32)
        faiss.normalize_L2(query)

        k = min(k, self.index.ntotal)
        distances, indices = self.index.search(query, k)

        results = []
        for i, (dist, idx) in enumerate(zip(distances[0], indices[0])):
            if idx >= 0 and idx in self.id_to_msg:
                message_id = self.id_to_msg[idx]
                results.append((message_id, float(dist)))

        logger.debug(f"Vector search: found {len(results)} results")
        return results

    def remove(self, message_id: int):
        # FAISS IndexFlatIP doesn't support removal
        # For now, just remove from mapping (vector stays but won't be returned)
        if message_id in self.msg_to_id:
            faiss_id = self.msg_to_id.pop(message_id)
            self.id_to_msg.pop(faiss_id, None)
            self.save()
            logger.debug(f"Removed message {message_id} from mapping")

    def clear(self):
        """清空向量索引（用于重建）"""
        self.index = faiss.IndexFlatIP(self.dimension)
        self.id_to_msg = {}
        self.msg_to_id = {}
        self.next_id = 0
        self.save()
        logger.info("Vector index cleared")

    def get_stats(self) -> dict:
        """获取向量库统计信息"""
        return {
            "total_vectors": self.index.ntotal if self.index else 0,
            "mapped_messages": len(self.msg_to_id),
            "dimension": self.dimension,
            "index_path": str(self.index_path),
        }

    def get_indexed_ids(self) -> set[int]:
        """获取已索引的消息 ID 集合"""
        return set(self.msg_to_id.keys())
