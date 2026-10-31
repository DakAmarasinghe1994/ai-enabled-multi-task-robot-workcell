import cv2
import numpy as np
import pyzed.sl as sl
import math
from ultralytics import YOLO
from pydexarm import Dexarm
import time
import sys
import signal
import socket
import threading
import pickle 
import pandas as pd
import random
import json
import global_var
from scipy import stats
from ColorDetection1 import color_det1
#from SizeDetection import size_det
from collections import Counter


def iou(box1, box2):
    x1, y1, x2, y2 = box1
    x1b, y1b, x2b, y2b = box2
    inter_x1 = max(x1, x1b)
    inter_y1 = max(y1, y1b)
    inter_x2 = min(x2, x2b)
    inter_y2 = min(y2, y2b)
    inter_area = max(0, inter_x2 - inter_x1 + 1) * max(0, inter_y2 - inter_y1 + 1)
    box1_area = (x2 - x1 + 1) * (y2 - y1 + 1)
    box2_area = (x2b - x1b + 1) * (y2b - y1b + 1)
    union_area = box1_area + box2_area - inter_area
    return inter_area / union_area if union_area > 0 else 0

def velocity_track(current_time, positions, prev_positions):
    # Threshold value can be changed
    vel_threshold = 30  # mm/s or m/s depending on units
    velocity_status = {}
    for obj_id, position in positions:
        if obj_id in prev_positions:
            prev_pos, prev_time = prev_positions[obj_id]
            dt = current_time - prev_time
            if dt > 0:
                dx = np.array(position) - np.array(prev_pos)
                velocity = dx / dt
                is_vel = np.all(np.abs(velocity) <= vel_threshold)
                velocity_status[obj_id] = is_vel
            else:
                velocity_status[obj_id] = True  # No movement if dt=0
        else:
            velocity_status[obj_id] = True  # No previous data; assume stationary

        prev_positions[obj_id] = (position, current_time)

    return velocity_status

def get_orientation_minAreaRect(roi):
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, roi

    largest = max(contours, key=cv2.contourArea)
    rect = cv2.minAreaRect(largest)
    box = np.intp(cv2.boxPoints(rect))
    cv2.drawContours(roi, [box], 0, (0, 255, 0), 2)

    angle = rect[2]
    if rect[1][0] < rect[1][1]:
        angle = 90 + angle

    center = tuple(np.intp(rect[0]))
    cv2.putText(roi, f"{angle:.2f} deg", (center[0] + 10, center[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)

    return angle, roi

def signal_handler(sig, frame):
    print()
    print("! Interruption signal received. Cleaning up.")
    global interrupted
    interrupted = True

def client_handler(conn, addr, client_id):
    global client1_conn, client2_conn
    print(f"[Server] Client {client_id} connected: {addr}")

    try:
        while True:
            time.sleep(0.5)
    except:
        pass
    finally:
        print(f"[Server] Client {client_id} disconnected: {addr}")
        with lock:
            if client_id == 1:
                client1_conn = None
            else:
                client2_conn = None
        conn.close()
        
def accept_clients(server_socket):
    global client1_conn, client2_conn
    while True:
        conn, addr = server_socket.accept()
        with lock:
            if client1_conn is None:
                client1_conn = conn
                threading.Thread(target=client_handler, args=(conn, addr, 1), daemon=True).start()
            elif client2_conn is None:
                client2_conn = conn
                threading.Thread(target=client_handler, args=(conn, addr, 2), daemon=True).start()
            else:
                print(f"[Server] Rejecting extra client: {addr}")
                conn.sendall(b"Server full. Only two clients allowed.\n")
                conn.close()

def handle_commands():
    COMMAND_HOST = '127.0.0.1'
    COMMAND_PORT = 65433

    command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    command_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    command_socket.bind((COMMAND_HOST, COMMAND_PORT))
    command_socket.listen()

    print(f"[Command Server] Listening for commands on {COMMAND_HOST}:{COMMAND_PORT}")

    while True:
        conn, addr = command_socket.accept()
        print(f"[Command Server] Connection from {addr}")
        with conn:
            try:
                data = conn.recv(1024).decode().strip()
                if not data:
                    continue

                command = json.loads(data)

                if command.get("cmd") == "verify":
                    print("[Command Server] Received verification request.")

                    label = command.get("label")
                    x = command.get("x")
                    y = command.get("y")
                    z = command.get("z")
                    conf_thres = 0.6  # or retrieve dynamically

                    # Capture a new ZED frame
                    if zed.grab(runtime_parameters) == sl.ERROR_CODE.SUCCESS:
                        zed.retrieve_image(mat, sl.VIEW.LEFT)
                        zed.retrieve_measure(xyz_coord, sl.MEASURE.XYZ)
                        check_frame = mat.get_data()
                        check_rgb = cv2.cvtColor(check_frame, cv2.COLOR_BGRA2RGB)
                        check_results = model(check_rgb)[0]

                        object_still_present = False
                        for result in check_results.boxes:
                            conf = float(result.conf[0])
                            cls_id = int(result.cls[0])
                            check_label = model.names[cls_id]

                            if check_label != label or conf < conf_thres:
                                continue

                            check_x1, check_y1, check_x2, check_y2 = map(int, result.xyxy[0].tolist())
                            check_cx, check_cy = (check_x1 + check_x2) // 2, (check_y1 + check_y2) // 2
                            err, check_point3D = xyz_coord.get_value(check_cx, check_cy)

                            if err != sl.ERROR_CODE.SUCCESS or np.isnan(check_point3D[0]):
                                continue

                            dx = abs(check_point3D[0] - x)
                            dy = abs(check_point3D[1] - y)
                            dz = abs(check_point3D[2] - z)

                            print(f"[Verification] dx: {dx:.2f}, dy: {dy:.2f}, dz: {dz:.2f}")

                            if dx < 35 and dy < 35 and dz < 35:
                                object_still_present = True
                                break

                        if not object_still_present:
                            response = {"success": True, "message": f"{label} picked up successfully"}
                        else:
                            response = {"success": False, "message": f"{label} still detected. Retry needed"}

                        conn.sendall((json.dumps(response) + '\n').encode())
                        print(f"[Command Server] Sent response: {response}")

                else:
                    print("[Command Server] Unknown command received.")
                    conn.sendall(json.dumps({"success": False, "error": "Unknown command"}).encode())

            except Exception as e:
                print(f"[Command Server] Error: {e}")
                conn.sendall(json.dumps({"success": False, "error": str(e)}).encode())

# Stop the program if interrupted
signal.signal(signal.SIGINT, signal_handler)
interrupted = False

# Real-time data transmission
HOST = '127.0.0.1'
PORT = 65432
#global prev_positions
#global tool_details    
#global rob_coord
global client1_conn, client2_conn
lock = threading.Lock()
client1_conn = None
client2_conn = None
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_socket.bind((HOST, PORT))
server_socket.listen()
print(f"[Server] Listening on {HOST}:{PORT} (max 2 clients)")
threading.Thread(target=accept_clients, args=(server_socket,), daemon=True).start()


threading.Thread(target=handle_commands, daemon=True).start()


last_data1 = None
last_data2 = None

# Variables for size detection
step = 3
lower_thres = 83
upper_thres = 124
noise_val = 8
t_lors = ""

# Load the Yolov8 Module
model = YOLO('my_model1.pt')

# Initialize the Zed Camera
zed = sl.Camera()
init_params = sl.InitParameters()
init_params.camera_resolution = sl.RESOLUTION.HD720
init_params.depth_mode = sl.DEPTH_MODE.ULTRA
init_params.coordinate_units = sl.UNIT.MILLIMETER

status = zed.open(init_params)
if status != sl.ERROR_CODE.SUCCESS:
    print(f"ZED Error: {status}")
    sys.exit(1)

runtime_parameters = sl.RuntimeParameters()
mat = sl.Mat()
xyz_coord = sl.Mat()
prev_positions = {}

conf_thres = 0.6
allowed_labels = {"Hammer", "Wrench", "Screwdriver"}

print("! Starting main loop. Press Ctrl+C to exit the program.")

count = 1
while not interrupted:
    if zed.grab(runtime_parameters) == sl.ERROR_CODE.SUCCESS:
        zed.retrieve_image(mat, sl.VIEW.LEFT)
        zed.retrieve_measure(xyz_coord, sl.MEASURE.XYZ)
        frame = mat.get_data()
        new_frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)

        results = model(new_frame)[0]
        detections = []

        for result in results.boxes:
            conf = float(result.conf[0])
            cls_id = int(result.cls[0])
            label = model.names[cls_id]

            if conf < conf_thres or label not in allowed_labels:
                continue

            x1, y1, x2, y2 = map(int, result.xyxy[0].tolist())
            area = (x2 - x1) * (y2 - y1)
            if area > 100000:  # Filter large boxes (feet, body)
                continue

            # Exclude near bottom of frame (feet area)
            if y1 > frame.shape[0] - 100:
                continue

            detections.append({
                "conf": conf,
                "label": label,
                "box": (x1, y1, x2, y2),
                "cls": cls_id,
                "id": id(result)  # Unique identifier for velocity tracking
                #"id" : count
            })
            count += 1
        # Remove overlapping detections
        final_detections = []
        used = [False] * len(detections)
        for i in range(len(detections)):
            if used[i]:
                continue
            best = detections[i]
            for j in range(i + 1, len(detections)):
                if used[j]:
                    continue
                if iou(detections[i]["box"], detections[j]["box"]) > 0.5:
                    if detections[j]["conf"] > best["conf"]:
                        best = detections[j]
                    used[j] = True
            final_detections.append(best)
            used[i] = True

        current_time = time.time()
        positions = []
        detection_info = []

        # First and only loop
        for det in final_detections:
            x1, y1, x2, y2 = det["box"]
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            obj_id = det["id"]
            label = det["label"]

            err, point3D = xyz_coord.get_value(cx, cy)
            if err != sl.ERROR_CODE.SUCCESS or np.isnan(point3D[0]):
                continue
            x, y, z = point3D[:3]
            positions.append((obj_id, (x, y, z)))

            detection_info.append({
                "det": det,
                "cx": cx,
                "cy": cy,
                "x": x,
                "y": y,
                "z": z,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "label": label
            })

        velocity_status = velocity_track(current_time, positions, prev_positions)

        # Now process all detections with full context
        for info in detection_info:
            x, y, z = info["x"], info["y"], info["z"]
            x1, y1, x2, y2 = info["x1"], info["y1"], info["x2"], info["y2"]
            cx, cy = info["cx"], info["cy"]
            label = info["label"]
            obj_id = info["det"]["id"]

            x_coord = x - 35
            y_coord = 325 - y
            z_coord = -117

            is_vel_zero = velocity_status.get(obj_id, True)

            # Calibrate the size of the tool
            ys, xs = np.mgrid[y1:y2:step, x1:x2:step]
            coords = np.stack((xs.ravel(), ys.ravel()), axis=-1)
            points_3d = []
            for i_coord, j_coord in coords:
                err, point = xyz_coord.get_value(int(i_coord), int(j_coord))
                if err == sl.ERROR_CODE.SUCCESS and not np.isnan(point[0]):
                    points_3d.append(point)
            if len(points_3d) >= 3:
                points_3d = np.array(points_3d)
                # Remove outliers via percentiles
                mask = (
                    (points_3d[:, 0] >= np.percentile(points_3d[:, 0], 5)) &
                    (points_3d[:, 0] <= np.percentile(points_3d[:, 0], 95)) &
                    (points_3d[:, 1] >= np.percentile(points_3d[:, 1], 5)) &
                    (points_3d[:, 1] <= np.percentile(points_3d[:, 1], 95)) &
                    (points_3d[:, 2] >= np.percentile(points_3d[:, 2], 5)) &
                    (points_3d[:, 2] <= np.percentile(points_3d[:, 2], 95))
                )
                points_3d = points_3d[mask]
                width = np.max(points_3d[:, 0]) - np.min(points_3d[:, 0])
                height = np.max(points_3d[:, 1]) - np.min(points_3d[:, 1])
                depth = np.max(points_3d[:, 2]) - np.min(points_3d[:, 2])
            else:
                width = 0
                height = 0 
                depth = 0
            tool_size = [round(width, 2), round(height, 2), round(depth, 2)]
            max_tool_size = max(tool_size)
            if max_tool_size <= (lower_thres + noise_val):
                t_lors = "Small"
            elif max_tool_size > (lower_thres + noise_val) and max_tool_size <= (upper_thres - noise_val):
                t_lors = "Medium"
            else:
                t_lors = "Large"
            
            
            # Determine the color of the tool 
            roi = new_frame[y1:y2, x1:x2]
            tool_col = color_det1(roi)  # Your custom color detection

            # Get the rotation angle of the end-effector with the orientation of the detected tool
            angle, annotated_roi = get_orientation_minAreaRect(roi)
            if angle is not None:
                ee_ang = round(angle + 15)
                #ee_ang = 15
            else:
                ee_ang = 15

            rob_coord = {
                    "rx" : x_coord,
                    "ry" : y_coord,
                    "rz" : z_coord,
                    "cx" : x,
                    "cy" : y,
                    "cz" : z,
                    "ea" : ee_ang,
                    #"vel" : is_vel_zero
                    "label" : label
                }
            tool_details = {
                    "Type" : label,
                    "Color" : tool_col,
                    "Size(mm)" : tool_size,
                    "Size Category" : t_lors
                }
            with lock:
                    if client1_conn and tool_details != last_data1:
                        try:
                            message1 = json.dumps(tool_details) + '\n'
                            client1_conn.sendall(message1.encode())
                            print(f"[Server] Sent to Client 1: {tool_details}")
                            last_data1 = tool_details
                        except:
                            print("[Server] Error sending to Client 1")
                            client1_conn = None

                    if client2_conn and rob_coord != last_data2:
                        try:
                            #rob_coord["label"] = label 
                            message2 = json.dumps(rob_coord) + '\n'
                            client2_conn.sendall(message2.encode())
                            print(f"[Server] Sent to Client 2: {rob_coord}")
                            last_data2 = rob_coord                                                            
                        
                            #threading.Thread(target=handle_commands, daemon=True).start()
                        
                        except:
                            print("[Server] Error sending to Client 2")
                            client2_conn = None

                    time.sleep(0.3)  # Real-time data transferring frequency
                
            # Print the information of the detected tool
            print()
            print("The information of the detected tool")
            print(f"Type: {label}")
            print(f"Color: {tool_col}")
            print(f"Size(mm): W={width:.2f}, H={height:.2f}, D={depth:.2f}")
            print(f"Camera coordinates(mm): X={x:.0f}, Y={y:.0f}, Z={z:.0f}")
            print(f"Robot coordinates(mm): X={x_coord:.0f}, Y={y_coord:.0f}, Z={z_coord:.0f}")
            #print(f"Velocity near zero: {is_vel_zero}")
            
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.circle(frame, (cx, cy), 3, (0, 0, 255), -1)
            cv2.putText(frame, f'{label} [{x:.0f}, {y:.0f}, {z:.0f}mm]',
                        (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        #cv2.imshow("ZED + YOLOv8", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            interrupted = True

print("[Sender] Disconnected!")
zed.close()
cv2.destroyAllWindows()


"""
# Extra functions that were used for testing
def get_dominant_color(roi):
    if roi.size == 0:
        return (0, 0, 0)
    roi = cv2.resize(roi, (50, 50))
    pixels = roi.reshape((-1, 3))
    pixels = [tuple(pixel) for pixel in pixels]
    most_common = Counter(pixels).most_common(1)
    return most_common[0][0]  # RGB tuple
   
def rgb_to_name(rgb):
    # Basic mapping for simple colors
    # You can expand this with more precise ranges
    r, g, b = rgb
    if r > 200 and g < 100 and b < 100:
        return "Red"
    elif r < 100 and g > 200 and b < 100:
        return "Green"
    elif r < 100 and g < 100 and b > 200:
        return "Blue"
    elif r > 200 and g > 200 and b < 100:
        return "Yellow"
    elif r > 200 and g > 200 and b > 200:
        return "White"
    elif r < 50 and g < 50 and b < 50:
        return "Black"
    elif r > 150 and g < 100 and b > 150:
        return "Magenta"
    elif r < 150 and g > 150 and b > 150:
        return "Cyan"
    else:
        return "Unknown"

def detect_orientation_minAreaRect(roi):
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, roi

    # Use the largest contour by area
    largest_contour = max(contours, key=cv2.contourArea)

    # Get min area rect (rotated rectangle)
    rect = cv2.minAreaRect(largest_contour)
    box = cv2.boxPoints(rect)
    box = np.intp(box)

    # Draw box on ROI
    annotated = roi.copy()
    cv2.drawContours(annotated, [box], 0, (0, 255, 0), 2)

    # Extract angle
    angle = rect[2]

    # Normalize angle to [0, 180)
    if rect[1][0] < rect[1][1]:  # width < height
        angle = 90 + angle
    else:
        angle = angle

    # Display angle
    center = tuple(np.intp(rect[0]))
    cv2.putText(annotated, f"{angle:.2f} deg", (center[0] + 10, center[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)

    return angle, annotated

def velocity_track(current_time, positions, prev_positions, vel_threshold=15):
    all_within_threshold = True  # Assume True, invalidate if any object exceeds
    
    for obj_id, position in positions:
        position = np.array(position)

        if obj_id in prev_positions:
            prev_pos, prev_time = prev_positions[obj_id]
            prev_pos = np.array(prev_pos)
            dt = current_time - prev_time

            if dt > 0:
                velocity = (position - prev_pos) / dt
                if np.any(np.abs(velocity) > vel_threshold):
                    all_within_threshold = False
        else:
            # First time seeing this object, cannot compute velocity yet
            pass

        # Always update the current position/time
        prev_positions[obj_id] = (position, current_time)
    
    return all_within_threshold
    
def velocity_track(current_time, positions, prev_positions, vel_threshold=50):
    
    #Track the velocity of multiple objects.
    #Returns a dictionary {obj_id: is_stationary (True/False)}
    
    result = {}
    
    for obj_id, position in positions:
        position = np.array(position, dtype=np.float32)

        if obj_id in prev_positions:
            prev_pos, prev_time = prev_positions[obj_id]
            prev_pos = np.array(prev_pos, dtype=np.float32)
            dt = current_time - prev_time

            if dt > 0:
                velocity = (position - prev_pos) / dt  # in mm/s
                speed = np.linalg.norm(velocity)       # scalar velocity
                result[obj_id] = speed < vel_threshold
            else:
                result[obj_id] = False  # Cannot calculate velocity
        else:
            result[obj_id] = False  # Not enough data yet

        # Always update the latest position
        prev_positions[obj_id] = (position, current_time)
    
    return result
"""
