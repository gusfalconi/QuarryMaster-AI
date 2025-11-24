import google.generativeai as genai
import gspread
import json
import requests
import io
import time
from datetime import datetime
from PIL import Image
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# --- CONFIGURATION (EDIT THIS) ---
GEMINI_KEY = "PASTE_YOUR_AIZA_KEY_HERE"
SHEET_NAME = "Quarry Log Master"
FOLDER_ID = "PASTE_YOUR_DRIVE_FOLDER_ID_HERE"
CREDENTIALS_FILE = '/home/admin/quarry_system/credentials.json'

# CAMERA SETUP
CAM_IP = "192.168.1.100"  # Check UniFi for actual IP
CAM_USER = "admin"
CAM_PASS = "your_camera_password"

# --- SYSTEM SETUP ---
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
gc = gspread.authorize(creds)
drive_service = build('drive', 'v3', credentials=creds)

def get_monthly_sheet():
    """Manages Monthly Tabs automatically"""
    current_month = datetime.now().strftime("%B %Y")
    sh = gc.open(SHEET_NAME)
    try:
        return sh.worksheet(current_month)
    except:
        print(f"New Month! Creating tab: {current_month}")
        ws = sh.add_worksheet(title=current_month, rows=5000, cols=10)
        sh.reorder_worksheets([ws] + sh.worksheets())
        ws.append_row(["Date", "Time", "Direction", "Type", "Color", "Plate", "Tarp?", "Load", "Material", "Snapshot"])
        ws.freeze(rows=1)
        ws.set_column_width(9, 300)
        return ws

def capture_and_log():
    # 1. Capture Snapshot from Reolink
    url = f"http://{CAM_IP}/cgi-bin/api.cgi?cmd=Snap&channel=0&user={CAM_USER}&password={CAM_PASS}"
    try:
        response = requests.get(url, stream=True, timeout=5)
        if response.status_code != 200: 
            print("Cam Offline"); return
        img = Image.open(response.raw)
    except Exception as e:
        print(f"Connection Error: {e}"); return

    # 2. Save Temp for AI
    img.save("temp.jpg")

    # 3. AI Analysis
    prompt = """
    Analyze this quarry vehicle. Return JSON only:
    1. "vehicle_type": "Dump Truck", "Pickup", "Car", "Motorcycle", "Other".
    2. "color": Dominant color (or "Unknown" if B&W).
    3. "direction": "ENTERING" (facing camera) or "EXITING" (facing away).
    4. "plate": License Text (Use "?" for obscured).
    5. "tarp_status": "YES", "NO", "PARTIAL", "N/A".
    6. "load_status": "Loaded", "Empty", "Unknown".
    7. "load_desc": If visible, describe material (e.g. "Blue Rocks"). If Tarp is YES, say "Covered".
    """
    
    try:
        ai_resp = model.generate_content([prompt, "temp.jpg"])
        text = ai_resp.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        
        # Filter: Ignore empty roads or random motion
        if data['vehicle_type'] == "Other": 
            print(".", end="", flush=True)
            return

        print(f"\n🚀 DETECTED: {data['vehicle_type']} | {data['plate']}")

        # 4. Process Image (Crop & Upload)
        width, height = img.size
        new_width = width * 0.7 # Keep center 70%
        left = (width - new_width) / 2
        cropped = img.crop((left, 0, left + new_width, height))
        cropped.thumbnail((400, 400))
        
        img_byte_arr = io.BytesIO()
        cropped.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        
        file_meta = {'name': f"{data['plate']}_{int(time.time())}.jpg", 'parents': [FOLDER_ID]}
        media = MediaIoBaseUpload(img_byte_arr, mimetype='image/jpeg')
        file = drive_service.files().create(body=file_meta, media_body=media, fields='id').execute()
        drive_service.permissions().create(fileId=file.get('id'), body={'role': 'reader', 'type': 'anyone'}).execute()
        link = f"https://drive.google.com/uc?export=view&id={file.get('id')}"

        # 5. Log to Sheet
        sheet = get_monthly_sheet()
        ts = datetime.now()
        sheet.append_row([
            str(ts.date()), str(ts.time()), 
            data.get("direction"), data.get("vehicle_type"), data.get("color"),
            data.get("plate"), data.get("tarp_status"), 
            data.get("load_status"), data.get("load_desc"), 
            f'=IMAGE("{link}")'
        ], value_input_option='USER_ENTERED')
        
        print("✅ Logged.")

    except Exception as e:
        print(f"Error: {e}")

# --- MAIN LOOP ---
print("System Online. Watching...")
while True:
    capture_and_log()
    time.sleep(5)
