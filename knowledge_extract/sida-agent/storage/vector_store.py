from pathlib import Path

from langchain_chroma import Chroma
from config import get_embedding_model
from logger import get_logger

log = get_logger()

# 默认持久化目录：进程退出后向量库仍在，支持跨运行累积与独立只读问答。
_DEFAULT_PERSIST_DIR = str(
    Path(__file__).resolve().parent.parent / "output" / "vector_db"
)


def get_vector_store(
    collection_name: str = "science_kb",
    persist_directory: str | None = _DEFAULT_PERSIST_DIR,
) -> Chroma:
    """初始化并获取 Chroma 向量库实例（默认全科合集 science_kb）。

    persist_directory 默认为 output/vector_db（本地持久化，跨进程累积）；
    显式传 None 则回退为纯内存临时库。

    写入的每个 Document 的 metadata 至少包含：
    - id：与图谱节点键一致的全局键（subject:Kind:name），供图谱检索后回表取全文；
    - subject / type：学科与实体种类，便于按学科过滤。
    """
    log.debug(
        "[vector_store] 初始化 Chroma 向量库, collection=%s, persist=%s",
        collection_name, persist_directory,
    )
    embeddings = get_embedding_model()
    return Chroma(
        collection_name=collection_name,
        embedding_function=embeddings,
        persist_directory=persist_directory,
    )