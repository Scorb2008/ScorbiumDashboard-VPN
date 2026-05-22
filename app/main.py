import uvicorn
from app.core.config import config


def main() -> None:
    uvicorn.run(
        "app.core.server:create_app",
        factory=True,
        host=config.web.server_host,
        port=config.web.server_port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
