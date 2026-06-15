from src.haircut_bot.bootstrap import build_service
from src.haircut_bot.server import run_server


def main() -> None:
    config, service = build_service()
    run_server(config, service)


if __name__ == "__main__":
    main()
