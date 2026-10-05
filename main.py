import time
import os
import json
import pytz
import gspread
import threading
import requests
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# --- CONFIGURACIÓN ---
ZONA_HORARIA = pytz.timezone("Europe/Madrid")
SPREADSHEET_NAME = "Renfe_Dataset_Live" 

GTFS_VEHICLE_POSITIONS_URL = "https://gtfsrt.renfe.com/vehicle_positions.json"

app = FastAPI()

# --- CONEXIÓN GLOBAL PERSISTENTE A GOOGLE SHEETS ---
print("🔐 Inicializando conexión persistente con Google Sheets...")
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_json_string = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    
    if not creds_json_string:
        raise ValueError("La variable de entorno GOOGLE_CREDENTIALS_JSON no está configurada.")
        
    cred_dict = json.loads(creds_json_string)
    creds_global = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, scope)
    client_global = gspread.authorize(creds_global)
    SHEET_GLOBAL = client_global.open(SPREADSHEET_NAME).get_worksheet(0)
    
    # Comprobar y asegurar los encabezados en la primera fila
    encabezados = [
        "timestamp_captura", "firma_unica", "route_id", 
        "trip_id", "vehicle_id", "lat", "lon", "current_status"
    ]
    
    fila_1 = SHEET_GLOBAL.row_values(1)
    if not fila_1 or fila_1[0] != "timestamp_captura":
        SHEET_GLOBAL.insert_row(encabezados, 1)
        print("📌 Encabezados insertados correctamente en la primera fila.")
        
    print("✅ Conexión con Google Sheets establecida y lista.")
except Exception as e:
    print(f"❌ Error al conectar con Sheets al inicio: {e}")
    client_global = None
    SHEET_GLOBAL = None

def ejecutar_extraccion_cercanias():
    global SHEET_GLOBAL, client_global
    
    if SHEET_GLOBAL is None:
        print("⚠️ Hoja no conectada. Reintentando conexión inicial...")
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds_json_string = os.environ.get("GOOGLE_CREDENTIALS_JSON")
            cred_dict = json.loads(creds_json_string)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, scope)
            client_global = gspread.authorize(creds)
            SHEET_GLOBAL = client_global.open(SPREADSHEET_NAME).get_worksheet(0)
            
            fila_1 = SHEET_GLOBAL.row_values(1)
            encabezados = [
                "timestamp_captura", "firma_unica", "route_id", 
                "trip_id", "vehicle_id", "lat", "lon", "current_status"
            ]
            if not fila_1 or fila_1[0] != "timestamp_captura":
                SHEET_GLOBAL.insert_row(encabezados, 1)
        except Exception as e:
            print(f"⛔ Error en Sheets al reconectar: {e}")
            return

    try:
        total_filas = SHEET_GLOBAL.row_count
        inicio_lectura = max(1, total_filas - 300)
        data_reciente = SHEET_GLOBAL.get_values(f"A{inicio_lectura}:N{total_filas}")
        firmas_existentes = {str(r[1]) for r in data_reciente if len(r) > 1}

        registros_crudos = []
        intentos_api = 0
        while intentos_api < 3:
            try:
                res = requests.get(GTFS_VEHICLE_POSITIONS_URL, timeout=15)
                res.raise_for_status()
                data_json = res.json()
                registros_crudos = data_json.get('entity', [])
                break
            except Exception as e_api:
                intentos_api += 1
                print(f"⚠️ Error al conectar con Renfe Data (Intento {intentos_api}/3): {e_api}")
                time.sleep(5)

        if not registros_crudos:
            print("ℹ️ No se obtuvieron datos nuevos en este ciclo.")
            return

        ahora = datetime.now(ZONA_HORARIA)
        timestamp_captura = ahora.strftime("%Y-%m-%d %H:%M:%S")
        nuevos_registros = []

        for item in registros_crudos:
            trip_update = item.get('vehicle', {})
            trip_info = trip_update.get('trip', {})
            
            trip_id = trip_info.get('trip_id', 'N/D')
            route_id = trip_info.get('route_id', 'N/D')
            vehicle_id = trip_update.get('vehicle', {}).get('id', 'N/D')
            
            position = trip_update.get('position', {})
            lat = position.get('latitude', 0.0)
            lon = position.get('longitude', 0.0)
            
            current_status = trip_update.get('current_status', 'N/D')
            
            firma_unica = f"{trip_id}_{vehicle_id}_{timestamp_captura[:16]}"
            
            if firma_unica not in firmas_existentes:
                nuevos_registros.append([
                    timestamp_captura,
                    firma_unica,
                    route_id,
                    trip_id,
                    vehicle_id,
                    lat,
                    lon,
                    current_status
                ])

        if nuevos_registros:
            intentos = 0
            while intentos < 3:
                try:
                    SHEET_GLOBAL.append_rows(nuevos_registros)
                    print(f"✅ DATASET CERCANÍAS ACTUALIZADO: {len(nuevos_registros)} registros inyectados.")
                    break
                except Exception as e_sheet:
                    intentos += 1
                    print(f"🔄 Error de API de Google Sheets (Intento {intentos}/3): {e_sheet}")
                    time.sleep(5)
                    try:
                        creds_json_string = os.environ.get("GOOGLE_CREDENTIALS_JSON")
                        cred_dict = json.loads(creds_json_string)
                        creds = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
                        client_global = gspread.authorize(creds)
                        SHEET_GLOBAL = client_global.open(SPREADSHEET_NAME).get_worksheet(0)
                    except:
                        pass

    except Exception as e:
        print(f"❌ Error crítico en el ciclo de extracción: {e}")

# --- ENDPOINTS FASTAPI ---

@app.get("/")
def home():
    return {"status": "online", "msg": "Recolector Cercanías ML - Operativo"}

@app.get("/ping")
def ping():
    return {"status": "alive", "timestamp": datetime.now(ZONA_HORARIA).isoformat()}

@app.get("/recolectar")
def recolectar():
    threading.Thread(target=ejecutar_extraccion_cercanias).start()
    return {"status": "started", "msg": "Extracción de Cercanías iniciada en background"}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
