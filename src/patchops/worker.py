import argparse
import logging
from time import sleep

from .config import Settings
from .service import work_once
from .store import Store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = Settings()
    store = Store(settings)
    if args.once:
        work_once(store, settings)
        return
    while True:
        if work_once(store, settings) is None:
            sleep(1)


if __name__ == "__main__":
    main()
