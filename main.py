#!/usr/bin/env python3
import os
from http.server import ThreadingHTTPServer


def main():
    if not os.environ.get("BENCHMARK_PROXY_CONTROL_URL"):
        os.environ["BENCHMARK_PROXY_CONTROL_URL"] = "http://127.0.0.1:8082"

    from app.runner import BenchmarkApp
    from app.server import create_handler
    from app.settings import SERVER_HOST, SERVER_PORT

    app = BenchmarkApp()
    handler = create_handler(app)
    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), handler)
    print(f"Server listening on http://{SERVER_HOST}:{SERVER_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
