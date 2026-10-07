import time
import os
import pytz
import threading
import requests
import psycopg2
from psycopg2.extras import execute_values
from fastapi import FastAPI
from datetime import datetime

# --- CONFIGURACIÓN ---
ZONA_HORARIA = pytz.timezone("Europe/Madrid")

# --- ENDPOINTS OFICIALES RENFE ---
URL_POSICIONES = "https://gtfsrt.renfe.com/vehicle_positions.json"
URL_HORARIOS = "https://gtfsrt.renfe.com/trip_updates.json"
URL_ALERTAS = "https://gtfsrt.renfe.com/alerts.json"

app = FastAPI()

def get_db_connection():
    # Obtiene la cadena de conexión desde Render
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("La variable DATABASE_URL no está configurada.")
    return psycopg2.connect(db_url)

# --- MÓDULO 1: POSICIONES ---
def ejecutar_extraccion_posiciones():
    try:
        res = requests.get(URL_POSICIONES, timeout=15)
        res.raise_for_status()
        registros_crudos = res.json().get('entity', [])

        ahora = datetime.now(ZONA_HORARIA)
        timestamp_captura = ahora.strftime("%Y-%m-%d %H:%M:%S")
        nuevos_registros = []
        
        # Diccionario local temporal para no añadir duplicados en el mismo JSON
        firmas_locales = set() 

        for item in registros_crudos:
            entity_id = item.get('id', 'N/D')
            vehicle_data = item.get('vehicle', {})
            trip_info = vehicle_data.get('trip', {})
            trip_id = trip_info.get('tripId', entity_id)
            
            route_id = str(entity_id).split('-')[0].replace('VP_', '') if str(entity_id).startswith('VP_') else 'N/D'
                
            vehicle_obj = vehicle_data.get('vehicle', {})
            vehicle_id = vehicle_obj.get('id', 'N/D')
            position = vehicle_data.get('position', {})
            lat = position.get('latitude', 0.0)
            lon = position.get('longitude', 0.0)
            current_status = vehicle_data.get('currentStatus', 'EN_RUTA')
            
            firma_unica = f"{trip_id}_{vehicle_id}_{timestamp_captura[:16]}"
            
            if firma_unica not in firmas_locales:
                nuevos_registros.append((timestamp_captura, firma_unica, route_id, trip_id, vehicle_id, lat, lon, current_status))
                firmas_locales.add(firma_unica)

        if nuevos_registros:
            conn = get_db_connection()
            cur = conn.cursor()
            # ON CONFLICT DO NOTHING evita duplicados en base de datos de forma nativa
            insert_query = """
                INSERT INTO posiciones_live (timestamp_captura, firma_unica, route_id, trip_id, vehicle_id, lat, lon, current_status) 
                VALUES %s ON CONFLICT (firma_unica) DO NOTHING
            """
            execute_values(cur, insert_query, nuevos_registros)
            conn.commit()
            cur.close()
            conn.close()
            print(f"📍 Posiciones SQL: {len(nuevos_registros)} procesadas.")

    except Exception as e:
        print(f"❌ Error en extracción de POSICIONES SQL: {e}")

# --- MÓDULO 2: HORARIOS Y RETRASOS ---
def ejecutar_extraccion_horarios():
    try:
        res = requests.get(URL_HORARIOS, timeout=15)
        res.raise_for_status()
        registros_crudos = res.json().get('entity', [])

        ahora = datetime.now(ZONA_HORARIA)
        timestamp_captura = ahora.strftime("%Y-%m-%d %H:%M:%S")
        nuevos_registros = []
        firmas_locales = set()

        for item in registros_crudos:
            entity_id = item.get('id', 'N/D')
            trip_update = item.get('tripUpdate', {})
            trip_info = trip_update.get('trip', {})
            
            trip_id = trip_info.get('tripId', entity_id)
            estado_viaje = trip_info.get('scheduleRelationship', 'SCHEDULED')
            
            delay = trip_update.get('delay', None)
            stop_id = 'N/D'
            
            stop_time_updates = trip_update.get('stopTimeUpdate', [])
            if stop_time_updates:
                stop_id = stop_time_updates[0].get('stopId', 'N/D')
                if delay is None:
                    arrival = stop_time_updates[0].get('arrival', {})
                    delay = arrival.get('delay', None)
            
            firma_unica = f"{trip_id}_{timestamp_captura[:16]}"
            
            if firma_unica not in firmas_locales:
                nuevos_registros.append((timestamp_captura, firma_unica, trip_id, estado_viaje, stop_id, delay))
                firmas_locales.add(firma_unica)

        if nuevos_registros:
            conn = get_db_connection()
            cur = conn.cursor()
            insert_query = """
                INSERT INTO horarios_live (timestamp_captura, firma_unica, trip_id, estado_viaje, stop_id, retraso_segundos) 
                VALUES %s ON CONFLICT (firma_unica) DO NOTHING
            """
            execute_values(cur, insert_query, nuevos_registros)
            conn.commit()
            cur.close()
            conn.close()
            print(f"⏱️ Horarios SQL: {len(nuevos_registros)} procesados.")

    except Exception as e:
        print(f"❌ Error en extracción de HORARIOS SQL: {e}")

# --- MÓDULO 3: ALERTAS ---
def ejecutar_extraccion_alertas():
    try:
        res = requests.get(URL_ALERTAS, timeout=15)
        res.raise_for_status()
        registros_crudos = res.json().get('entity', [])

        ahora = datetime.now(ZONA_HORARIA)
        timestamp_captura = ahora.strftime("%Y-%m-%d %H:%M:%S")
        fecha_corta = ahora.strftime("%Y-%m-%d")
        nuevos_registros = []
        firmas_locales = set()

        for item in registros_crudos:
            alert_id = item.get('id', 'N/D')
            tipo_alerta = alert_id.split('_')[0] if '_' in alert_id else 'UNKNOWN'
            alert_data = item.get('alert', {})
            
            informed_entities = alert_data.get('informedEntity', [])
            afectados = []
            for e in informed_entities:
                if 'routeId' in e: afectados.append(str(e['routeId']))
                elif 'stopId' in e: afectados.append(str(e['stopId']))
            entidades_afectadas = ",".join(afectados) if afectados else "N/D"
            
            desc_texts = alert_data.get('descriptionText', {}).get('translation', [])
            descripcion = 'N/D'
            for dt in desc_texts:
                if dt.get('language') == 'es':
                    descripcion = dt.get('text', 'N/D')
                    break
            if descripcion == 'N/D' and desc_texts:
                 descripcion = desc_texts[0].get('text', 'N/D')
                 
            firma_unica = f"{alert_id}_{fecha_corta}"
            
            if firma_unica not in firmas_locales:
                nuevos_registros.append((timestamp_captura, firma_unica, alert_id, tipo_alerta, entidades_afectadas, descripcion))
                firmas_locales.add(firma_unica)

        if nuevos_registros:
            conn = get_db_connection()
            cur = conn.cursor()
            insert_query = """
                INSERT INTO alertas_live (timestamp_captura, firma_unica, alert_id, tipo_alerta, entidades_afectadas, descripcion) 
                VALUES %s ON CONFLICT (firma_unica) DO NOTHING
            """
            execute_values(cur, insert_query, nuevos_registros)
            conn.commit()
            cur.close()
            conn.close()
            print(f"⚠️ Alertas SQL: {len(nuevos_registros)} procesadas.")

    except Exception as e:
        print(f"❌ Error en extracción de ALERTAS SQL: {e}")

# --- ENDPOINTS FASTAPI ---
@app.get("/")
def home():
    return {"status": "online", "msg": "Recolector SQL Cercanías - Operativo"}

@app.get("/recolectar_todo")
def recolectar_todo():
    threading.Thread(target=ejecutar_extraccion_posiciones).start()
    threading.Thread(target=ejecutar_extraccion_horarios).start()
    threading.Thread(target=ejecutar_extraccion_alertas).start()
    return {"status": "started", "msg": "Extracción SQL paralela iniciada."}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
