import re
import distro


# 获取 linux 的 distro 和 major version
def get_linux_distro_version() -> tuple[str, str]:
    """Get the Linux distribution name and major version.

    Returns:
        tuple[str, str]: A tuple containing the distribution name and major version.
    """
    distro_name = distro.id()
    version = distro.version()
    major_version = version.split(".")[0] if version else ""
    return distro_name, major_version
