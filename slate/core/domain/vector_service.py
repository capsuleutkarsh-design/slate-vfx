import logging
import os
from typing import List, Optional
from fastembed import TextEmbedding

logger = logging.getLogger(__name__)

class VectorService:
    """
    Offline Semantic Embedding Service using fastembed.
    Generates high-quality vector embeddings without PyTorch dependencies.
    """
    
    _instance = None
    _model = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(VectorService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized: return
        self._model_name = "BAAI/bge-small-en-v1.5"
        
        # We define a local directory so it bundles into PyInstaller properly
        self._cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "models",
            "fastembed_cache"
        )
        os.makedirs(self._cache_dir, exist_ok=True)
        self._initialized = True

    def _ensure_model(self):
        if self._model is None:
            logger.info("VectorService: Loading fastembed model into memory...")
            try:
                # This will download the ~90MB ONNX model if not present in cache_dir
                self._model = TextEmbedding(
                    model_name=self._model_name,
                    cache_dir=self._cache_dir,
                    threads=4
                )
                logger.info("VectorService: Model loaded successfully.")
            except Exception as e:
                logger.error(f"VectorService: Failed to load model: {e}", exc_info=True)

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """Generates a 384-dimension vector for the given text."""
        if not text or not text.strip():
            return None
            
        self._ensure_model()
        if not self._model:
            return None
            
        try:
            # fastembed embed() returns a generator of numpy arrays
            embeddings = list(self._model.embed([text]))
            if embeddings:
                return embeddings[0].tolist()
            return None
        except Exception as e:
            logger.error(f"VectorService: Failed to generate embedding: {e}")
            return None

vector_service = VectorService()
