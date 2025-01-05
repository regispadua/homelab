import paho.mqtt.client as mqtt
import requests
import json
import os
import logging
import datetime
import time
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# Configurações do GLPI
glpi_base_url = 'http://192.168.1.23/apirest.php'
glpi_login_url = f'{glpi_base_url}/initSession'
glpi_logout_url = f'{glpi_base_url}/killSession'
glpi_ticket_url = f'{glpi_base_url}/ticket'
glpi_app_token = os.getenv('GLPI_APP_TOKEN', 'SEU_TOKEN_AQUI')
glpi_user_token = os.getenv('GLPI_USER_TOKEN', 'SEU_TOKEN_AQUI')

# Configurações do MQTT
mqtt_broker = '192.168.1.3'
mqtt_topic = 'frigate/events'
mqtt_user = 'USER'
mqtt_password = os.getenv('MQTT_PASSWORD', 'PASSWORD')

# Configuração de logging
logging.basicConfig(
    filename='mqtt_glpi.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Função para iniciar sessão no GLPI
def init_glpi_session():
    headers = {
        'App-Token': glpi_app_token,
        'Authorization': f'user_token {glpi_user_token}'
    }
    response = requests.get(glpi_login_url, headers=headers)
    if response.status_code == 200:
        session_token = response.json().get('session_token')
        logging.info("Sessão iniciada no GLPI.")
        return session_token
    else:
        logging.error(f"Erro ao iniciar sessão no GLPI: {response.status_code}, {response.text}")
        return None

# Função para encerrar sessão no GLPI
def kill_glpi_session(session_token):
    headers = {
        'App-Token': glpi_app_token,
        'Session-Token': session_token
    }
    response = requests.get(glpi_logout_url, headers=headers)
    if response.status_code == 200:
        logging.info("Sessão encerrada no GLPI.")
    else:
        logging.error(f"Erro ao encerrar sessão no GLPI: {response.status_code}, {response.text}")

# Função para validar IDs de entidades e categorias
def validate_ids(session_token):
    headers = {
        'App-Token': glpi_app_token,
        'Session-Token': session_token
    }

    # Obter entidades
    try:
        entities_response = requests.get(f'{glpi_base_url}/Entity', headers=headers)
        entities_response.raise_for_status()
        entities = entities_response.json()
        logging.info("Entidades verificadas com sucesso.")
        logging.debug(f"Resposta das entidades: {entities}")
        entity_id = entities[0]['id'] if entities else None
        logging.info(f"ID da entidade encontrado: {entity_id}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Erro ao verificar entidades: {e}")
        return None, None

    # Obter categorias
    try:
        categories_response = requests.get(f'{glpi_base_url}/ITILCategory', headers=headers)
        categories_response.raise_for_status()
        categories = categories_response.json()
        logging.info("Categorias verificadas com sucesso.")
        logging.debug(f"Resposta das categorias: {categories}")
        category_id = categories[0]['id'] if categories else None
        logging.info(f"ID da categoria encontrado: {category_id}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Erro ao verificar categorias: {e}")
        return entity_id, None

    return entity_id, category_id

# Define o cliente MQTT
client = mqtt.Client(protocol=mqtt.MQTTv311)
client.username_pw_set(mqtt_user, mqtt_password)

# Callback para logs MQTT
def on_log(client, userdata, level, buf):
    logging.debug(f"Log MQTT: {buf}")

client.on_log = on_log

# Callback para conexão
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info("Conectado ao broker MQTT com sucesso.")
        client.subscribe(mqtt_topic)
    else:
        logging.error(f"Falha ao conectar ao broker MQTT. Código: {rc}")

client.on_connect = on_connect

# Callback para mensagens
def on_message(client, userdata, msg):
    session_token = init_glpi_session()
    if not session_token:
        logging.error("Não foi possível iniciar a sessão no GLPI.")
        return

    entity_id, category_id = validate_ids(session_token)
    if entity_id is None or category_id is None:
        logging.error("IDs de entidades e/ou categorias inválidos.")
        kill_glpi_session(session_token)
        return

    try:
        event_data = json.loads(msg.payload)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logging.info(f"[{timestamp}] Evento recebido: {event_data}")

        # Preparar dados do ticket
        ticket_data = {
            'input': {
                'name': f"Evento: {event_data.get('event', 'Desconhecido')}",
                'content': f"Detalhes do evento: {json.dumps(event_data, indent=2)}",
                'status': '1',
                'urgency': '3',
                'priority': '2',
                'impact': '2',
                'type': '1',  # Certifique-se de que este tipo é válido
                'entities_id': entity_id,
                'itilcategories_id': category_id
            }
        }

        # Log dos dados do ticket antes de enviar
        logging.debug(f"Dados do ticket que será enviados: {json.dumps(ticket_data, indent=2)}")

        headers = {
            'Content-Type': 'application/json',
            'App-Token': glpi_app_token,
            'Session-Token': session_token
        }

        # Enviar dados para o GLPI
        response = requests.post(glpi_ticket_url, headers=headers, json=ticket_data)
        if response.status_code == 201:
            logging.info("Ticket criado com sucesso.")
        else:
            logging.error(f"Falha ao criar ticket: {response.status_code}, {response.text}")

    except json.JSONDecodeError as e:
        logging.error(f"Erro ao decodificar JSON: {e}")
    except Exception as e:
        logging.error(f"Erro inesperado ao processar a mensagem: {e}")
    finally:
        kill_glpi_session(session_token)

client.on_message = on_message

# Loop principal do MQTT
try:
    client.connect(mqtt_broker, 1883, 60)
    client.loop_forever()
except Exception as e:
    logging.critical(f"Erro ao conectar ao broker MQTT: {e}")
