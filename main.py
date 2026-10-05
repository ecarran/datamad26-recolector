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
        except Exception as e:
            print(f"⛔ Error en Sheets al reconectar: {e}")
            return

    try:
        # 1. VERIFICACIÓN DE ENCABEZADOS
        encabezados = [
            "timestamp_captura", "firma_unica", "route_id", 
            "trip_id", "vehicle_id", "lat", "lon", "current_status"
        ]
        
        try:
            fila_1 = SHEET_GLOBAL.row_values(1)
        except Exception:
            fila_1 = []
            
        if not fila_1 or fila_1[0] != "timestamp_captura":
            print("📌 Encabezados no detectados en la fila 1. Insertando...")
            SHEET_GLOBAL.insert_row(encabezados, 1)
            time.sleep(1)

        # 2. LECTURA DE DATOS RECIENTES (Para evitar duplicados)
        total_filas = SHEET_GLOBAL.row_count
        inicio_lectura = max(1, total_filas - 300)
        
        try:
            data_reciente = SHEET_GLOBAL.get_values(f"A{inicio_lectura}:H{max(1, total_filas)}")
        except Exception:
            data_reciente = []
            
        firmas_existentes = {str(r[1]) for r in data_reciente if len(r) > 1}

        # 3. PETICIÓN A LA FUENTE DE RENFE
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
            print("ℹ️ No se obtuvieron datos nuevos.")
            return

        # 4. PROCESAMIENTO EXACTO BASADO EN EL JSON DE RENFE
        ahora = datetime.now(ZONA_HORARIA)
        timestamp_captura = ahora.strftime("%Y-%m-%d %H:%M:%S")
        nuevos_registros = []

        for item in registros_crudos:
            entity_id = item.get('id', 'N/D')
            vehicle_data = item.get('vehicle', {})
            
            # Bloque Trip (usa tripId)
            trip_info = vehicle_data.get('trip', {})
            trip_id = trip_info.get('tripId', entity_id)
            
            # La ruta no viene en el JSON, hay que extraerla de "VP_C1-23566"
            route_id = 'N/D'
            if str(entity_id).startswith('VP_'):
                route_id = str(entity_id).split('-')[0].replace('VP_', '')
                
            # Bloque Vehículo
            vehicle_obj = vehicle_data.get('vehicle', {})
            vehicle_id = vehicle_obj.get('id', 'N/D')
            
            # Coordenadas
            position = vehicle_data.get('position', {})
            lat = position.get('latitude', 0.0)
            lon = position.get('longitude', 0.0)
            
            # Estado (usa currentStatus, por defecto EN_RUTA si viene vacío)
            current_status = vehicle_data.get('currentStatus', 'EN_RUTA')
            
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

        # 5. ESCRITURA EN GOOGLE SHEETS
        if nuevos_registros:
            intentos = 0
            while intentos < 3:
                try:
                    SHEET_GLOBAL.append_rows(nuevos_registros)
                    print(f"✅ DATASET CERCANÍAS ACTUALIZADO: {len(nuevos_registros)} registros.")
                    break
                except Exception as e_sheet:
                    intentos += 1
                    print(f"🔄 Error de API (Intento {intentos}/3): {e_sheet}")
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
        print(f"❌ Error crítico en extracción: {e}")

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
    return {"status": "started", "msg": "Extracción de Cercanías iniciada"}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
