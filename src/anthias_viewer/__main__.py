import logging

from anthias_viewer import main

if __name__ == '__main__':
    try:
        main()
    except Exception:
        logging.exception('Viewer crashed.')
        raise
