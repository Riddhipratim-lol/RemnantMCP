import os
import voyageai
from typing import List

class VoyageClient:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("VOYAGE_API_KEY")
        if not self.api_key:
            raise ValueError("VOYAGE_API_KEY environment variable is not set.")
        self.client = voyageai.Client(api_key=self.api_key)
        self.model_name = "voyage-code-3"
        
    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for a list of strings using Voyage Code 3.
        """
        if not texts:
            return []
        try:
            result = self.client.embed(texts, model=self.model_name, input_type="document")
            return result.embeddings
        except Exception as e:
            print(f"Voyage AI embedding error: {e}")
            raise e
