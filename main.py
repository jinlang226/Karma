"""HTTP/UI backend entrypoint. Forwards to ``karma.interfaces.http.server``."""

from karma.interfaces.http.server import main

if __name__ == "__main__":
    main()
