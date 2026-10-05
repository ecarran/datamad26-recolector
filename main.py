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

# --- ENDPOINTS OFICIALES RENFE ---
URL_POSICIONES = "https://gtfsrt.renfe.com/vehicle_positions.json"
URL_HORARIOS = "https://gtfsrt.renfe.com/trip_updates.json"
URL_ALERTAS = "https://gtfsrt.renfe.com/alerts.json"

app = FastAPI()

# --- CONEXIÓN GLOBAL PERSISTENTE A GOOGLE SHEETS ---
client_global = None
SHEET_POSICIONES = None
SHEET_HORARIOS = None
SHEET_ALERTAS = None

def inicializar_conexion_sheets():
    global client_global, SHEET_POSICIONES, SHEET_HORARIOS, SHEET_ALERTAS
    print("🔐 Inicializando conexión persistente con Google Sheets...")
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_json_string = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        
        if not creds_json_string:
            raise ValueError("La variable de entorno GOOGLE_CREDENTIALS_JSON no está configurada.")
            
        cred_dict = json.loads(creds_json_string)
        creds_global = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, scope)
        client_global = gspread.authorize(creds_global)
        workbook = client_global.open(SPREADSHEET_NAME)
        
        # 1. Pestaña Posiciones (Hoja 0)
        SHEET_POSICIONES = workbook.get_worksheet(0)
        
        # 2. Pestaña Horarios (Hoja 1) - Crea si no existe
        try:
            SHEET_HORARIOS = workbook.get_worksheet(1)
            if SHEET_HORARIOS.title != "Horarios_Live":
                SHEET_HORARIOS.update_title("Horarios_Live")
        except Exception:
            SHEET_HORARIOS = workbook.add_worksheet(title="Horarios_Live", rows="1000", cols="10")
            
        # 3. Pestaña Alertas (Hoja 2) - Crea si no existe
        try:
            SHEET_ALERTAS = workbook.get_worksheet(2)
            if SHEET_ALERTAS.title != "Alertas_Live":
                SHEET_ALERTAS.update_title("Alertas_Live")
        except Exception:
            SHEET_ALERTAS = workbook.add_worksheet(title="Alertas_Live", rows="1000", cols="10")

        print("✅ Conexión con Google Sheets establecida y pestañas validadas.")
        return True
    except Exception as e:
        print(f"❌ Error al conectar con Sheets al inicio: {e}")
        return False

# Inicialización en el arranque
inicializar_conexion_sheets()

# --- MÓDULO 1: POSICIONES (El original intacto) ---
def ejecutar_extraccion_posiciones():
    global SHEET_POSICIONES
    if SHEET_POSICIONES is None and not inicializar_conexion_sheets():
        return

    try:
        encabezados = ["timestamp_captura", "firma_unica", "route_id", "trip_id", "vehicle_id", "lat", "lon", "current_status"]
        try: fila_1 = SHEET_POSICIONES.row_values(1)
        except Exception: fila_1 = []
            
        if not fila_1 or fila_1[0] != "timestamp_captura":
            SHEET_POSICIONES.insert_row(encabezados, 1)
            time.sleep(1)

        total_filas = SHEET_POSICIONES.row_count
        inicio_lectura = max(1, total_filas - 300)
        try: data_reciente = SHEET_POSICIONES.get_values(f"A{inicio_lectura}:H{max(1, total_filas)}")
        except Exception: data_reciente = []
        firmas_existentes = {str(r[1]) for r in data_reciente if len(r) > 1}

        res = requests.get(URL_POSICIONES, timeout=15)
        res.raise_for_status()
        registros_crudos = res.json().get('entity', [])

        ahora = datetime.now(ZONA_HORARIA)
        timestamp_captura = ahora.strftime("%Y-%m-%d %H:%M:%S")
        nuevos_registros = []

        for item in registros_crudos:
            entity_id = item.get('id', 'N/D')
            vehicle_data = item.get('vehicle', {})
            trip_info = vehicle_data.get('trip', {})
            trip_id = trip_info.get('tripId', entity_id)
            
            route_id = 'N/D'
            if str(entity_id).startswith('VP_'):
                route_id = str(entity_id).split('-')[0].replace('VP_', '')
                
            vehicle_obj = vehicle_data.get('vehicle', {})
            vehicle_id = vehicle_obj.get('id', 'N/D')
            position = vehicle_data.get('position', {})
            lat = position.get('latitude', 0.0)
            lon = position.get('longitude', 0.0)
            current_status = vehicle_data.get('currentStatus', 'EN_RUTA')
            
            firma_unica = f"{trip_id}_{vehicle_id}_{timestamp_captura[:16]}"
            
            if firma_unica not in firmas_existentes:
                nuevos_registros.append([timestamp_captura, firma_unica, route_id, trip_id, vehicle_id, lat, lon, current_status])

        if nuevos_registros:
            SHEET_POSICIONES.append_rows(nuevos_registros)
            print(f"📍 Posiciones: {len(nuevos_registros)} registros inyectados.")

    except Exception as e:
        print(f"❌ Error en extracción de POSICIONES: {e}")


# --- MÓDULO 2: HORARIOS Y RETRASOS ---
def ejecutar_extraccion_horarios():
    global SHEET_HORARIOS
    if SHEET_HORARIOS is None and not inicializar_conexion_sheets():
        return

    try:
        encabezados = ["timestamp_captura", "firma_unica", "trip_id", "estado_viaje", "stop_id", "retraso_segundos"]
        try: fila_1 = SHEET_HORARIOS.row_values(1)
        except Exception: fila_1 = []
            
        if not fila_1 or fila_1[0] != "timestamp_captura":
            SHEET_HORARIOS.insert_row(encabezados, 1)
            time.sleep(1)

        total_filas = SHEET_HORARIOS.row_count
        inicio_lectura = max(1, total_filas - 300)
        try: data_reciente = SHEET_HORARIOS.get_values(f"A{inicio_lectura}:F{max(1, total_filas)}")
        except Exception: data_reciente = []
        firmas_existentes = {str(r[1]) for r in data_reciente if len(r) > 1}

        res = requests.get(URL_HORARIOS, timeout=15)
        res.raise_for_status()
        registros_crudos = res.json().get('entity', [])

        ahora = datetime.now(ZONA_HORARIA)
        timestamp_captura = ahora.strftime("%Y-%m-%d %H:%M:%S")
        nuevos_registros = []

        for item in registros_crudos:
            entity_id = item.get('id', 'N/D')
            trip_update = item.get('tripUpdate', {})
            trip_info = trip_update.get('trip', {})
            
            trip_id = trip_info.get('tripId', entity_id)
            estado_viaje = trip_info.get('scheduleRelationship', 'SCHEDULED')
            
            delay = trip_update.get('delay', 'N/D')
            stop_id = 'N/D'
            
            stop_time_updates = trip_update.get('stopTimeUpdate', [])
            if stop_time_updates:
                stop_id = stop_time_updates[0].get('stopId', 'N/D')
                if delay == 'N/D':
                    arrival = stop_time_updates[0].get('arrival', {})
                    delay = arrival.get('delay', 'N/D')
            
            firma_unica = f"{trip_id}_{timestamp_captura[:16]}"
            
            if firma_unica not in firmas_existentes:
                nuevos_registros.append([timestamp_captura, firma_unica, trip_id, estado_viaje, stop_id, delay])

        if nuevos_registros:
            SHEET_HORARIOS.append_rows(nuevos_registros)
            print(f"⏱️ Horarios: {len(nuevos_registros)} registros inyectados.")

    except Exception as e:
        print(f"❌ Error en extracción de HORARIOS: {e}")


# --- MÓDULO 3: INCIDENCIAS Y AVISOS ---
def ejecutar_extraccion_alertas():
    global SHEET_ALERTAS
    if SHEET_ALERTAS is None and not inicializar_conexion_sheets():
        return

    try:
        encabezados = ["timestamp_captura", "firma_unica", "alert_id", "tipo_alerta", "entidades_afectadas", "descripcion"]
        try: fila_1 = SHEET_ALERTAS.row_values(1)
        except Exception: fila_1 = []
            
        if not fila_1 or fila_1[0] != "timestamp_captura":
            SHEET_ALERTAS.insert_row(encabezados, 1)
            time.sleep(1)

        # Buscamos en las últimas 500 filas para que no se duplique la alerta el mismo día
        total_filas = SHEET_ALERTAS.row_count
        inicio_lectura = max(1, total_filas - 500)
        try: data_reciente = SHEET_ALERTAS.get_values(f"A{inicio_lectura}:F{max(1, total_filas)}")
        except Exception: data_reciente = []
        firmas_existentes = {str(r[1]) for r in data_reciente if len(r) > 1}

        res = requests.get(URL_ALERTAS, timeout=15)
        res.raise_for_status()
        registros_crudos = res.json().get('entity', [])

        ahora = datetime.now(ZONA_HORARIA)
        timestamp_captura = ahora.strftime("%Y-%m-%d %H:%M:%S")
        fecha_corta = ahora.strftime("%Y-%m-%d")
        nuevos_registros = []

        for item in registros_crudos:
            alert_id = item.get('id', 'N/D')
            tipo_alerta = alert_id.split('_')[0] if '_' in alert_id else 'UNKNOWN'
            
            alert_data = item.get('alert', {})
            
            # Extraer IDs afectados (Rutas o Paradas)
            informed_entities = alert_data.get('informedEntity', [])
            afectados = []
            for e in informed_entities:
                if 'routeId' in e: afectados.append(str(e['routeId']))
                elif 'stopId' in e: afectados.append(str(e['stopId']))
            entidades_afectadas = ",".join(afectados) if afectados else "N/D"
            
            # Extraer Texto en Español
            desc_texts = alert_data.get('descriptionText', {}).get('translation', [])
            descripcion = 'N/D'
            for dt in desc_texts:
                if dt.get('language') == 'es':
                    descripcion = dt.get('text', 'N/D')
                    break
            if descripcion == 'N/D' and desc_texts:
                 descripcion = desc_texts[0].get('text', 'N/D')
                 
            # Firma única de alerta (ID de la alerta + Fecha de hoy) para guardarla solo 1 vez al día
            firma_unica = f"{alert_id}_{fecha_corta}"
            
            if firma_unica not in firmas_existentes:
                nuevos_registros.append([timestamp_captura, firma_unica, alert_id, tipo_alerta, entidades_afectadas, descripcion])

        if nuevos_registros:
            SHEET_ALERTAS.append_rows(nuevos_registros)
            print(f"⚠️ Alertas: {len(nuevos_registros)} registros inyectados.")

    except Exception as e:
        print(f"❌ Error en extracción de ALERTAS: {e}")


# --- ENDPOINTS FASTAPI ---

@app.get("/")
def home():
    return {"status": "online", "msg": "Recolector Cercanías ML Modular - Operativo"}

@app.get("/ping")
def ping():
    return {"status": "alive", "timestamp": datetime.now(ZONA_HORARIA).isoformat()}

# Endpoint original (No lo borramos para no romper lo que ya tienes montado)
@app.get("/recolectar")
def recolectar_posiciones():
    threading.Thread(target=ejecutar_extraccion_posiciones).start()
    return {"status": "started", "msg": "Extracción de Posiciones iniciada en background"}

# Nuevos endpoints independientes
@app.get("/recolectar_horarios")
def recolectar_horarios():
    threading.Thread(target=ejecutar_extraccion_horarios).start()
    return {"status": "started", "msg": "Extracción de Horarios/Retrasos iniciada en background"}

@app.get("/recolectar_alertas")
def recolectar_alertas():
    threading.Thread(target=ejecutar_extraccion_alertas).start()
    return {"status": "started", "msg": "Extracción de Alertas iniciada en background"}

# Extra point: Endpoint maestro para disparar los 3 a la vez
@app.get("/recolectar_todo")
def recolectar_todo():
    threading.Thread(target=ejecutar_extraccion_posiciones).start()
    threading.Thread(target=ejecutar_extraccion_horarios).start()
    threading.Thread(target=ejecutar_extraccion_alertas).start()
    return {"status": "started", "msg": "Extracción paralela de Posiciones, Horarios y Alertas iniciada."}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
