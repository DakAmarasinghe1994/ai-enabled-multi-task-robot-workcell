import socket
import json
import global_var

HOST = '127.0.0.1'
PORT = 65432

def main():
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect((HOST, PORT))
    print("[Client#1] Connected to server.")

    buffer = ""
    while True:
        try:
            data = client.recv(1024).decode()
            if not data:
                break
            buffer += data

            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                try:
                    coords = json.loads(line.strip())              
                    
                    print()
                    print("The tool details are: ")
                    print(f"[Client] Received: {coords}")
                except json.JSONDecodeError:
                    print("[Client] Invalid JSON")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[Client] Error: {e}")
            break

    client.close()
    print("[Client#1] Disconnected!")

if __name__ == "__main__":
    main()

