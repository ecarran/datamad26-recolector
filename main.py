import os
import pytz
import threading
import requests
from fastapi import FastAPI
from datetime import datetime
from supabase import create_client, Client

# --- CONFIGURACIÓN ---
ZONA_HORARIA = pytz.timezone("Europe/Madrid")
URL_POSICIONES = "https://gtfsrt.renfe.com/vehicle_positions.json"
URL_HORARIOS = "https://gtfsrt.renfe.com/trip_updates.json"
URL_ALERTAS = "https://gtfsrt.renfe.com/alerts.json"

app = FastAPI()

# Inicializar cliente Supabase REST API
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")
if not supabase_url or not supabase_key:
    print("⚠️ Faltan variables de entorno SUPABASE_URL o SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

# --- MÓDULO 1: POSICIONES ---
def ejecutar_extraccion_posiciones():
    try:
        res = requests.get(URL_POSICIONES, timeout=15)
        res.raise_for_status()
        registros_crudos = res.json().get('entity', [])

        ahora = datetime.now(ZONA_HORARIA)
        timestamp_captura = ahora.strftime("%Y-%m-%d %H:%M:%S")
        nuevos_registros = []
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
            
            firma_unica = f"{trip_id}_{vehicle_id}_{timestamp_captura[:16]}"
            
            if firma_unica not in firmas_locales:
                nuevos_registros.append({
                    "timestamp_captura": timestamp_captura,
                    "firma_unica": firma_unica,
                    "route_id": route_id,
                    "trip_id": trip_id,
                    "vehicle_id": vehicle_id,
                    "lat": position.get('latitude', 0.0),
                    "lon": position.get('longitude', 0.0),
                    "current_status": vehicle_data.get('currentStatus', 'EN_RUTA')
                })
                firmas_locales.add(firma_unica)

        if nuevos_registros:
            # Upsert ignora duplicados usando la Primary Key (firma_unica)
            supabase.table("posiciones_live").upsert(
                nuevos_registros, on_conflict="firma_unica", ignore_duplicates=True
            ).execute()
            print(f"📍 Posiciones REST: {len(nuevos_registros)} procesadas.")

    except Exception as e:
        print(f"❌ Error en extracción de POSICIONES REST: {e}")

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
            delay = trip_update.get('delay', None)
            stop_id = 'N/D'
            
            stop_time_updates = trip_update.get('stopTimeUpdate', [])
            if stop_time_updates:
                stop_id = stop_time_updates[0].get('stopId', 'N/D')
                if delay is None:
                    delay = stop_time_updates[0].get('arrival', {}).get('delay', None)
            
            firma_unica = f"{trip_id}_{timestamp_captura[:16]}"
            
            if firma_unica not in firmas_locales:
                nuevos_registros.append({
                    "timestamp_captura": timestamp_captura,
                    "firma_unica": firma_unica,
                    "trip_id": trip_id,
                    "estado_viaje": trip_info.get('scheduleRelationship', 'SCHEDULED'),
                    "stop_id": stop_id,
                    "retraso_segundos": delay
                })
                firmas_locales.add(firma_unica)

        if nuevos_registros:
            supabase.table("horarios_live").upsert(
                nuevos_registros, on_conflict="firma_unica", ignore_duplicates=True
            ).execute()
            print(f"⏱️ Horarios REST: {len(nuevos_registros)} procesados.")

    except Exception as e:
        print(f"❌ Error en extracción de HORARIOS REST: {e}")

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
            alert_data = item.get('alert', {})
            
            afectados = [str(e.get('routeId') or e.get('stopId')) for e in alert_data.get('informedEntity', []) if 'routeId' in e or 'stopId' in e]
            
            desc_texts = alert_data.get('descriptionText', {}).get('translation', [])
            descripcion = next((dt.get('text') for dt in desc_texts if dt.get('language') == 'es'), 'N/D')
            if descripcion == 'N/D' and desc_texts: descripcion = desc_texts[0].get('text', 'N/D')
                 
            firma_unica = f"{alert_id}_{fecha_corta}"
            
            if firma_unica not in firmas_locales:
                nuevos_registros.append({
                    "timestamp_captura": timestamp_captura,
                    "firma_unica": firma_unica,
                    "alert_id": alert_id,
                    "tipo_alerta": alert_id.split('_')[0] if '_' in alert_id else 'UNKNOWN',
                    "entidades_afectadas": ",".join(afectados) if afectados else "N/D",
                    "descripcion": descripcion
                })
                firmas_locales.add(firma_unica)

        if nuevos_registros:
            supabase.table("alertas_live").upsert(
                nuevos_registros, on_conflict="firma_unica", ignore_duplicates=True
            ).execute()
            print(f"⚠️ Alertas REST: {len(nuevos_registros)} procesadas.")

    except Exception as e:
        print(f"❌ Error en extracción de ALERTAS REST: {e}")

# --- ENDPOINTS FASTAPI ---
@app.get("/")
def home():
    return {"status": "online", "msg": "Recolector REST Cercanías - Operativo"}

@app.get("/recolectar_todo")
def recolectar_todo():
    threading.Thread(target=ejecutar_extraccion_posiciones).start()
    threading.Thread(target=ejecutar_extraccion_horarios).start()
    threading.Thread(target=ejecutar_extraccion_alertas).start()
    return {"status": "started", "msg": "Extracción REST paralela iniciada."}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
