import logging

from anthias_viewer import main

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    try:
        main()
    except Exception:
        logger.exception('Viewer crashed.')
        raise
