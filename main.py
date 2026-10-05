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

# Nota: Reemplazar o ajustar con el endpoint oficial de Cercanías / GTFS Realtime de Renfe Data
GTFS_VEHICLE_POSITIONS_URL = "https://api.renfe.com/cercanias/vehicle_positions.json" # (Ejemplo de referencia)

app = FastAPI()

# --- CONEXIÓN GLOBAL PERSISTENTE A GOOGLE SHEETS (VÍA VARIABLES DE ENTORNO) ---
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
    
    # Reintento de conexión si falló al arrancar
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
        # 1. LECTURA DE DATOS RECIENTES EN LA HOJA (Para evitar duplicados)
        total_filas = SHEET_GLOBAL.row_count
        inicio_lectura = max(1, total_filas - 300)
        data_reciente = SHEET_GLOBAL.get_values(f"A{inicio_lectura}:N{total_filas}")
        firmas_existentes = {str(r[1]) for r in data_reciente if len(r) > 1}

        # 2. PETICIÓN A LA FUENTE DE RENFE DATAS (Cercanías)
        registros_crudos = []
        intentos_api = 0
        while intentos_api < 3:
            try:
                # res = requests.get(GTFS_VEHICLE_POSITIONS_URL, timeout=15)
                # res.raise_for_status()
                # registros_crudos = res.json().get('entity', [])
                break
            except Exception as e_api:
                intentos_api += 1
                print(f"⚠️ Error al conectar con Renfe Data (Intento {intentos_api}/3): {e_api}")
                time.sleep(5)

        if not registros_crudos:
            print("ℹ️ No se obtuvieron datos nuevos en este ciclo o pendiente de ajustar endpoint específico.")
            return

        # 3. PROCESAMIENTO Y PARSEO DE VARIABLES PARA ML
        ahora = datetime.now(ZONA_HORARIA)
        timestamp_captura = ahora.strftime("%Y-%m-%d %H:%M:%S")
        nuevos_registros = []

        for item in registros_crudos:
            # Parseo adaptado a la estructura GTFS Realtime
            # trip_id = item.get('trip', {}).get('trip_id', 'N/D')
            # delay = item.get('trip', {}).get('delay', 0)
            # firma_unica = f"{trip_id}_{timestamp_captura[:16]}"
            # if firma_unica not in firmas_existentes:
            #     nuevos_registros.append([timestamp_captura, firma_unica, ...])
            pass

        # 4. ESCRITURA EN GOOGLE SHEETS
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
