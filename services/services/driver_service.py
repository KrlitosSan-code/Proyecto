from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service

import config


def crear_driver():

    options = Options()

    options.add_argument(f"--user-data-dir={config.settings.EDGE_USER_DATA}")    
    options.add_argument(f"--profile-directory={config.settings.EDGE_PROFILE}")

    options.add_argument("--start-maximized")
    options.add_argument("--disable-extensions")
    options.add_argument("--download.default_directory=" + str(config.settings.DOWNLOADS_DIR))

    print("DRIVER_PATH:", config.settings.DRIVER_PATH)
    print("EXISTE:", config.settings.DRIVER_PATH.exists())
    service = Service(
        str(config.settings.DRIVER_PATH)
    )

    driver = webdriver.Edge(
        service=service,
        options=options
    )

    return driver