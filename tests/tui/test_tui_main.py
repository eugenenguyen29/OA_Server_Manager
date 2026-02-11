import random
import time
import threading
from textual.widgets import Log

from tui_main import AdminApp

server = None


class MockServer:
    def __init__(self):
        self.output_handler = None
        self.running = False
        self.network_manager = MockNetworkManager()
        self.game_state_manager = MockGameStateManager()
        self.obs_connection_manager = MockOBSManager()

    def set_output_handler(self, handler):
        self.output_handler = handler

    def send_command(self, command):
        if self.output_handler:
            self.output_handler(f"Server received: {command}")

    def set_async_loop(self, loop):
        pass

    def start_server(self):
        self.running = True

    def run_server_loop(self):
        while self.running:
            if self.output_handler:
                messages = [
                    "Player connected from 192.168.1.100",
                    "Match started on map dm17",
                    "Player fragged by rail gun",
                    "Bot added: Anarki",
                    "Latency changed to 50ms",
                    "OBS recording started",
                    "Player disconnected",
                    "Match ended",
                ]
                msg = random.choice(messages)
                self.output_handler(f"[{time.strftime('%H:%M:%S')}] {msg}")
            time.sleep(2)

    def dispose(self):
        self.running = False

    def kick_client(self, client_id: int):
        if self.output_handler:
            self.output_handler(f"Kicking client {client_id}")


class MockNetworkManager:
    def __init__(self):
        self.client_type_map = {0: "HUMAN", 1: "HUMAN", 2: "HUMAN", 3: "BOT"}
        self.client_name_map = {
            0: "Player1",
            1: "Player2",
            2: "Player3",
            3: "Bot_Anarki",
        }
        self.client_ip_map = {
            0: "192.168.1.100",
            1: "192.168.1.101",
            2: "192.168.1.102",
        }

    def get_client_count(self):
        return len(self.client_type_map)

    def get_human_count(self):
        return sum(1 for t in self.client_type_map.values() if t == "HUMAN")

    def get_bot_count(self):
        return sum(1 for t in self.client_type_map.values() if t == "BOT")


class MockGameStateManager:
    def get_current_state(self):
        class State:
            name = "WARMUP"

        return State()


class MockOBSManager:
    def is_client_connected(self, ip):
        return ip == "192.168.1.100"


class MockTUIApp(AdminApp):
    CSS_PATH = "../tui_main.tcss"

    def on_mount(self) -> None:
        global server
        server = MockServer()

        super().on_mount()

        app_log = self.query_one("#app-log", Log)
        app_log.write_line("Mock TUI started")
        app_log.write_line("Type commands to test input")
        app_log.write_line("Press 'q' to quit")

        server_thread = threading.Thread(target=server.run_server_loop, daemon=True)
        server_thread.start()


def main():
    app = MockTUIApp()
    app.run()


if __name__ == "__main__":
    main()
