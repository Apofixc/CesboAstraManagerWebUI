import sys
import signal
import logging

from App.init import Server

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def signal_handler(sig, frame):
    logging.info(f"Получен сигнал {sig}. Остановка приложения.")
    sys.exit(0)

# Регистрация обработчиков сигналов
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    server = Server()
    app = server.create_app()
    server.run()