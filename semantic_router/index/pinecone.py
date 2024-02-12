from pydantic.v1 import BaseModel, Field
import requests
import time
import hashlib
import os
from typing import Any, List, Tuple, Optional, Union
from semantic_router.index.base import BaseIndex
from semantic_router.utils.logger import logger
import numpy as np
from semantic_router.route import Route


def clean_route_name(route_name: str) -> str:
    return route_name.strip().replace(" ", "-")


class PineconeRecord(BaseModel):
    id: str = ""
    values: List[float]
    route: str
    utterance: str

    def __init__(self, **data):
        super().__init__(**data)
        # generate ID based on route name and utterances to prevent duplicates
        clean_route = clean_route_name(self.route)
        utterance_id = hashlib.md5(self.utterance.encode()).hexdigest()
        self.id = f"{clean_route}#{utterance_id}"

    def to_dict(self):
        return {
            "id": self.id,
            "values": self.values,
            "metadata": {"sr_route": self.route, "sr_utterance": self.utterance},
        }


class PineconeIndex(BaseIndex):
    index_prefix: str = "semantic-router--"
    index_name: str = "index"
    dimensions: Union[int, None] = None
    metric: str = "cosine"
    cloud: str = "aws"
    region: str = "us-west-2"
    host: str = ""
    client: Any = Field(default=None, exclude=True)
    index: Optional[Any] = Field(default=None, exclude=True)
    ServerlessSpec: Any = Field(default=None, exclude=True)

    def __init__(self, **data):
        super().__init__(**data)
        self._initialize_client()

        self.type = "pinecone"
        self.client = self._initialize_client()
        if not self.index_name.startswith(self.index_prefix):
            self.index_name = f"{self.index_prefix}{self.index_name}"
        # Create or connect to an existing Pinecone index
        self.index = self._init_index()

    def _initialize_client(self, api_key: Optional[str] = None):
        try:
            from pinecone import Pinecone, ServerlessSpec

            self.ServerlessSpec = ServerlessSpec
        except ImportError:
            raise ImportError(
                "Please install pinecone-client to use PineconeIndex. "
                "You can install it with: "
                "`pip install 'semantic-router[pinecone]'`"
            )
        api_key = api_key or os.getenv("PINECONE_API_KEY")
        if api_key is None:
            raise ValueError("Pinecone API key is required.")
        return Pinecone(api_key=api_key)

    def _init_index(self, force_create: bool = False) -> Union[Any, None]:
        index_exists = self.index_name in self.client.list_indexes().names()
        dimensions_given = self.dimensions is not None
        if dimensions_given and not index_exists:
            # if the index doesn't exist and we have dimension value
            # we create the index
            self.client.create_index(
                name=self.index_name,
                dimension=self.dimensions,
                metric=self.metric,
                spec=self.ServerlessSpec(cloud=self.cloud, region=self.region),
            )
            # wait for index to be created
            while not self.client.describe_index(self.index_name).status["ready"]:
                time.sleep(1)
            index = self.client.Index(self.index_name)
            time.sleep(0.5)
        elif index_exists:
            # if the index exists we just return it
            index = self.client.Index(self.index_name)
            # grab the dimensions from the index
            self.dimensions = index.describe_index_stats()["dimension"]
        elif force_create and not dimensions_given:
            raise ValueError(
                "Cannot create an index without specifying the dimensions."
            )
        else:
            # if the index doesn't exist and we don't have the dimensions
            # we return None
            logger.warning("Index could not be initialized.")
            index = None
        if index is not None:
            self.host = self.client.describe_index(self.index_name)["host"]
        return index
        
    def add(self, embeddings: List[List[float]], route: Route, utterances: List[str]):
        if self.index is None:
            self.dimensions = self.dimensions or len(embeddings[0])
            self.index = self._init_index(force_create=True)
        vectors_to_upsert = []
        for vector, utterance in zip(embeddings, utterances):
            record = PineconeRecord(values=vector, route=route.name, utterance=utterance)  # Use route.name
            vectors_to_upsert.append(record.to_dict())
        if self.index is not None:
            self.index.upsert(vectors=vectors_to_upsert)
        else:
            raise ValueError("Index is None could not upsert.")

    def _get_route_vecs(self, route_name: str):
        clean_route = clean_route_name(route_name)
        res = requests.get(
            f"https://{self.host}/vectors/list?prefix={clean_route}#",
            headers={"Api-Key": os.environ["PINECONE_API_KEY"]},
        )
        return [vec["id"] for vec in res.json()["vectors"]]

    def delete(self, route_name: str):
        route_vec_ids = self._get_route_vecs(route_name=route_name)
        if self.index is not None:
            self.index.delete(ids=route_vec_ids)
        else:
            raise ValueError("Index is None, could not delete.")

    def delete_all(self):
        self.index.delete(delete_all=True)

    def describe(self) -> dict:
        if self.index is not None:
            stats = self.index.describe_index_stats()
            return {
                "type": self.type,
                "dimensions": stats["dimension"],
                "vectors": stats["total_vector_count"],
            }
        else:
            raise ValueError("Index is None, cannot describe index stats.")

    def query(self, vector: np.ndarray, top_k: int = 5) -> Tuple[np.ndarray, List[str]]:
        if self.index is None:
            raise ValueError("Index is not populated.")
        query_vector_list = vector.tolist()
        results = self.index.query(
            vector=[query_vector_list],
            top_k=top_k,
            include_metadata=True,
        )
        scores = [result["score"] for result in results["matches"]]
        route_names = [result["metadata"]["sr_route"] for result in results["matches"]]
        return np.array(scores), route_names

    def delete_index(self):
        self.client.delete_index(self.index_name)

    def __len__(self):
        return self.index.describe_index_stats()["total_vector_count"]
