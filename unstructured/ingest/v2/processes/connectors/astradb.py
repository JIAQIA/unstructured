import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from unstructured import __name__ as integration_name
from unstructured.__version__ import __version__ as integration_version
from unstructured.ingest.enhanced_dataclass import enhanced_field
from unstructured.ingest.utils.data_prep import batch_generator
from unstructured.ingest.v2.interfaces import (
    AccessConfig,
    ConnectionConfig,
    FileData,
    UploadContent,
    Uploader,
    UploaderConfig,
    UploadStager,
    UploadStagerConfig,
)
from unstructured.ingest.v2.logger import logger
from unstructured.ingest.v2.processes.connector_registry import (
    DestinationRegistryEntry,
)
from unstructured.utils import requires_dependencies

if TYPE_CHECKING:
    from astrapy.db import AstraDBCollection

CONNECTOR_TYPE = "astradb"


@dataclass
class AstraDBAccessConfig(AccessConfig):
    token: str
    api_endpoint: str


@dataclass
class AstraDBConnectionConfig(ConnectionConfig):
    connection_type: str = CONNECTOR_TYPE
    access_config: AstraDBAccessConfig = enhanced_field(sensitive=True)


@dataclass
class AstraDBUploadStagerConfig(UploadStagerConfig):
    pass


@dataclass
class AstraDBUploadStager(UploadStager):
    upload_stager_config: AstraDBUploadStagerConfig = field(
        default_factory=lambda: AstraDBUploadStagerConfig()
    )

    def conform_dict(self, element_dict: dict) -> dict:
        return {
            "$vector": element_dict.pop("embeddings", None),
            "content": element_dict.pop("text", None),
            "metadata": element_dict,
        }

    def run(
        self,
        elements_filepath: Path,
        file_data: FileData,
        output_dir: Path,
        output_filename: str,
        **kwargs: Any,
    ) -> Path:
        with open(elements_filepath) as elements_file:
            elements_contents = json.load(elements_file)
        conformed_elements = []
        for element in elements_contents:
            conformed_elements.append(self.conform_dict(element_dict=element))
        output_path = Path(output_dir) / Path(f"{output_filename}.json")
        with open(output_path, "w") as output_file:
            json.dump(conformed_elements, output_file)
        return output_path


@dataclass
class AstraDBUploaderConfig(UploaderConfig):
    collection_name: str
    embedding_dimension: int
    namespace: Optional[str] = None
    requested_indexing_policy: Optional[dict[str, Any]] = None
    batch_size: int = 20


@dataclass
class AstraDBUploader(Uploader):
    connection_config: AstraDBConnectionConfig
    upload_config: AstraDBUploaderConfig
    connector_type: str = CONNECTOR_TYPE

    @requires_dependencies(["astrapy"], extras="astradb")
    def get_collection(self) -> "AstraDBCollection":
        from astrapy.db import AstraDB

        # Get the collection_name and embedding dimension
        collection_name = self.upload_config.collection_name
        embedding_dimension = self.upload_config.embedding_dimension
        requested_indexing_policy = self.upload_config.requested_indexing_policy

        # If the user has requested an indexing policy, pass it to the Astra DB
        options = {"indexing": requested_indexing_policy} if requested_indexing_policy else None

        # Build the Astra DB object.
        # caller_name/version for Astra DB tracking
        astra_db = AstraDB(
            api_endpoint=self.connection_config.access_config.api_endpoint,
            token=self.connection_config.access_config.token,
            namespace=self.upload_config.namespace,
            caller_name=integration_name,
            caller_version=integration_version,
        )

        # Create and connect to the newly created collection
        astra_db_collection = astra_db.create_collection(
            collection_name=collection_name,
            dimension=embedding_dimension,
            options=options,
        )
        return astra_db_collection

    def run(self, contents: list[UploadContent], **kwargs: Any) -> None:
        elements_dict = []
        for content in contents:
            with open(content.path) as elements_file:
                elements = json.load(elements_file)
                elements_dict.extend(elements)

        logger.info(
            f"writing {len(elements_dict)} objects to destination "
            f"collection {self.upload_config.collection_name}"
        )

        astra_batch_size = self.upload_config.batch_size
        collection = self.get_collection()

        for chunk in batch_generator(elements_dict, astra_batch_size):
            collection.insert_many(chunk)


astradb_destination_entry = DestinationRegistryEntry(
    connection_config=AstraDBConnectionConfig,
    upload_stager_config=AstraDBUploadStagerConfig,
    upload_stager=AstraDBUploadStager,
    uploader_config=AstraDBUploaderConfig,
    uploader=AstraDBUploader,
)
