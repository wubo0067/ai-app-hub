from .os import get_linux_distro_version


def main():
    distro, version = get_linux_distro_version()
    print(f"{distro} {version}")


if __name__ == "__main__":
    main()
