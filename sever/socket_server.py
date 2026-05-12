import socket

from config import settings 

class HTTPServer:
    def __init__(self, host=settings.HOST, port=settings.PORT):
        self.host = host
        self.port = port


    def _create_socket(self) -> socket.socket:

        # AF_INET  -> IPv4
        # SOCK_STREAM -> TCP
        sever_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sever_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sever_socket.bind((self.host, self.host))
        sever_socket.listen(settings.BACKLOG)
        print(f"[SERVER] Listening on {self.host}:{self.port}")

        return sever_socket


    def start(self):
        # loop: sock.accept() → submit client to thread pool

        pass

    def _handle_client(self, conn: socket.socket, addr: tuple):
        # recv raw bytes → parse → route → send response → close
        pass

    def stop(self):
       # set stop event, close server socket
       pass 