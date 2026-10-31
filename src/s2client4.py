import socket
import json
import time
import math
import threading
import queue
from pydexarm import Dexarm

# Configuration
HOST = '127.0.0.1'
PORT = 65432          # Receiver port from sender (main detection)
COMMAND_PORT = 65433  # Verification port (handle_commands in sender)

# Queue to hold only the latest valid command
command_queue = queue.Queue(maxsize=1)

# Flag to indicate if the arm is currently moving
arm_busy = threading.Event()

# Initialize DexArm
dexarm = Dexarm(port="/dev/ttyACM0")
dexarm.fast_move_to(0, 200, 0)
dexarm.dealy_s(0.2)
dexarm.soft_gripper_nature()
dexarm.dealy_s(0.2)
dexarm.abs_rotate(15)

def is_within_workspace(x, y, z):
    MAX_RADIUS = 360
    MIN_RADIUS = 245
    Z_MIN = -125
    Z_MAX = 135
    z = -z + 227
    r = math.sqrt(x**2 + y**2)
    return MIN_RADIUS <= r <= MAX_RADIUS and Z_MIN <= z <= Z_MAX

def send_verification_request(label, x, y, z):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as cmd_sock:
            cmd_sock.connect((HOST, COMMAND_PORT))
            request = {
                "cmd": "verify",
                "label": label,
                "x": x,
                "y": y,
                "z": z
            }
            cmd_sock.sendall((json.dumps(request) + "\n").encode())
            response = cmd_sock.recv(1024).decode().strip()
            return json.loads(response)
    except Exception as e:
        print(f"[Client#2] Verification error: {e}")
        return {"success": False, "error": str(e)}

def arm_control_loop():
    while True:
        coords = command_queue.get()
        arm_busy.set()
        try:
            x_coord = coords['rx']
            y_coord = coords['ry']
            z_coord = coords['rz']
            x = coords['cx']
            y = coords['cy']
            z = coords['cz']
            ee_ang = coords['ea']
            label = coords.get('label', 'Unknown')

            if is_within_workspace(x_coord, y_coord, z):
                print(f"\n[Arm] Grasp Attempt for: {label}")
                max_retries = 2
                attempt = 0
                success = False

                while attempt <= max_retries and not success:
                    print(f"[Arm] Attempt {attempt + 1} for {label}")
                    dexarm.abs_rotate(ee_ang)
                    dexarm.dealy_s(0.2)
                    dexarm.fast_move_to(x_coord, y_coord, z_coord)
                    dexarm.dealy_s(0.2)
                    dexarm.soft_gripper_pick()
                    dexarm.dealy_s(0.2)
                    dexarm.fast_move_to(0, 200, 0)
                    dexarm.dealy_s(0.2)

                    # Ask sender to verify if the object is gone
                    result = send_verification_request(label, x, y, z)
                    print(f"[Verification] Result: {result}")

                    if result.get("success"):
                        print(f"[SUCCESS] {label} picked up on attempt {attempt + 1}")
                        dexarm.fast_move_to(-200, 0, 30)
                        dexarm.dealy_s(0.2)
                        dexarm.soft_gripper_place()
                        dexarm.dealy_s(0.2)
                        dexarm.soft_gripper_nature()
                        dexarm.dealy_s(0.2)
                        dexarm.fast_move_to(0, 200, 0)
                        dexarm.dealy_s(0.2)
                        dexarm.abs_rotate(15)
                        dexarm.dealy_s(0.2)
                        success = True
                    else:
                        print(f"[RETRY] {label} still detected. Retrying...")
                        dexarm.soft_gripper_nature()
                        dexarm.dealy_s(0.2)
                        attempt += 1

                if not success:
                    print(f"[FAILURE] {label} could not be picked after {max_retries} attempts.")
                    dexarm.fast_move_to(0, 200, 0)
                    dexarm.soft_gripper_nature()
                    dexarm.abs_rotate(15)

            else:
                print("[Arm] Ignored: Out of workspace.")

        except Exception as e:
            print(f"[Arm] Error: {e}")
        finally:
            arm_busy.clear()
            command_queue.task_done()

def socket_listener():
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect((HOST, PORT))
    print("[Client#2] Connected to server.")
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
                    print(f"[Client#2] Received: {coords}")

                    if arm_busy.is_set():
                        print("[Client#2] Arm is busy. Discarding command.")
                        continue

                    # Add label to pass into verification
                    if 'Type' in coords:
                        coords['label'] = coords['Type']

                    # Clear old commands
                    while not command_queue.empty():
                        try:
                            command_queue.get_nowait()
                            command_queue.task_done()
                        except queue.Empty:
                            pass

                    command_queue.put_nowait(coords)

                except json.JSONDecodeError:
                    print("[Client#2] Invalid JSON.")
                except queue.Full:
                    print("[Client#2] Queue full. Ignoring extra command.")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[Client#2] Socket error: {e}")
            break

    client.close()
    print("[Client#2] Disconnected!")

def main():
    threading.Thread(target=arm_control_loop, daemon=True).start()
    socket_listener()

if __name__ == "__main__":
    main()

