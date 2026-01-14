def analysis_agent_system_prompt() -> str:
    return """
        You are a Linux kernel expert.
    """


def vmcore_detail_prompt() -> str:
    return """
        Below is the output from the crash tool analyzing a kdump, containing results from multiple commands:
        {vmcore_detail}
    """
