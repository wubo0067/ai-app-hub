from typing import TypedDict, List, Dict, Any, Optional

class _AgentStateOptional(TypedDict, total=False):
    """Agent 工作流中由各节点分步填充的可选字段"""
    target_subject: Optional[str]    # 判定出的学科：physics / chemistry / math
    target_concept: Optional[str]    # 提取的核心锚点实体（知识点）
    graph_context: Dict[str, Any]    # 图谱检索出的教研上下文（概念拆解/公式/实验/题型/例题）
    vector_chunks: List[str]        # 向量库回表拿出的原题全文
    final_answer: str               # 最终生成的系统讲解

class CircuitAgentState(_AgentStateOptional):
    """Agent 工作流状态：query 为入口必填，其余字段由节点逐步填充"""
    query: str                      # 用户提问