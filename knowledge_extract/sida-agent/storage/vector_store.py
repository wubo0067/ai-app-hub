from langchain_chroma import Chroma
from config import get_embedding_model
from logger import get_logger

log = get_logger()


def get_vector_store(collection_name: str = "science_kb") -> Chroma:
    """初始化并获取 Chroma 向量库实例（默认全科合集 science_kb）。

    写入的每个 Document 的 metadata 至少包含：
    - id：与图谱节点键一致的全局键（subject:Kind:name），供图谱检索后回表取全文；
    - subject / type：学科与实体种类，便于按学科过滤。
    """
    log.debug("[vector_store] 初始化 Chroma 向量库, collection=%s", collection_name)
    embeddings = get_embedding_model()
    return Chroma(collection_name=collection_name, embedding_function=embeddings)