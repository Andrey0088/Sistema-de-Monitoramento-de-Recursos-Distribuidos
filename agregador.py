import socket
import json
import threading
import mysql.connector
from flask import Flask, jsonify
from flask_cors import CORS

# --- CONFIGURAÇÕES DO BANCO DE DADOS ---
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': "Andrey@2",
    'database': 'monitoramento_db'
}

# --- CONFIGURAÇÕES DO SERVIDOR ---
HOST_TCP = '0.0.0.0' # Escuta em todas as interfaces de rede
PORTA_TCP = 9999
HOST_API = '0.0.0.0'
PORTA_API = 5000

# Limiares para alertas
LIMIAR_CPU_ALERTA = 90.0
LIMIAR_RAM_ALERTA = 85.0

# --- LÓGICA DO BANCO DE DADOS ---
def conectar_db():
    """Cria uma conexão com o banco de dados."""
    return mysql.connector.connect(**DB_CONFIG)

def salvar_dados(dados):
    """Salva os dados recebidos no banco de dados MySQL."""
    conn = conectar_db()
    cursor = conn.cursor()

    id_maquina = dados.get('id_maquina')

    if dados.get('tipo') == 'estatico':
        # Insere ou atualiza os dados estáticos da máquina
        sql = """
        INSERT INTO maquinas (id_maquina, info_cpu, memoria_total_gb, disco_total_gb)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        info_cpu = VALUES(info_cpu), memoria_total_gb = VALUES(memoria_total_gb), disco_total_gb = VALUES(disco_total_gb);
        """
        valores = (id_maquina, json.dumps(dados.get('info_cpu')), dados.get('memoria_total_gb'), dados.get('disco_total_gb'))
        cursor.execute(sql, valores)

    elif dados.get('tipo') == 'dinamico':
        # Insere os dados dinâmicos
        sql = """
        INSERT INTO metricas (id_maquina, cpu_uso_percent, ram_uso_percent, disco_uso_percent, temperatura_cpu)
        VALUES (%s, %s, %s, %s, %s);
        """
        valores = (id_maquina, dados.get('cpu_uso_percent'), dados.get('ram_uso_percent'), dados.get('disco_uso_percent'), dados.get('temperatura_cpu'))
        cursor.execute(sql, valores)
        
        # Atualiza o timestamp da máquina para saber que está ativa
        cursor.execute("UPDATE maquinas SET ultimo_contato = CURRENT_TIMESTAMP WHERE id_maquina = %s", (id_maquina,))

        # Verifica se algum limiar de alerta foi ultrapassado
        verificar_alertas(cursor, id_maquina, dados)

    conn.commit()
    cursor.close()
    conn.close()

def verificar_alertas(cursor, id_maquina, dados):
    """Verifica e insere alertas no banco de dados."""
    cpu = dados.get('cpu_uso_percent', 0)
    ram = dados.get('ram_uso_percent', 0)

    if cpu > LIMIAR_CPU_ALERTA:
        msg = f"Uso de CPU atingiu {cpu}%, ultrapassando o limiar de {LIMIAR_CPU_ALERTA}%."
        sql = "INSERT INTO alertas (id_maquina, tipo_alerta, valor_registrado, mensagem) VALUES (%s, %s, %s, %s)"
        cursor.execute(sql, (id_maquina, 'CPU_ALTA', cpu, msg))
        print(f"ALERTA GERADO para {id_maquina}: {msg}")

    if ram > LIMIAR_RAM_ALERTA:
        msg = f"Uso de RAM atingiu {ram}%, ultrapassando o limiar de {LIMIAR_RAM_ALERTA}%."
        sql = "INSERT INTO alertas (id_maquina, tipo_alerta, valor_registrado, mensagem) VALUES (%s, %s, %s, %s)"
        cursor.execute(sql, (id_maquina, 'RAM_ALTA', ram, msg))
        print(f"ALERTA GERADO para {id_maquina}: {msg}")

# --- LÓGICA DO SERVIDOR TCP ---
def manipular_cliente(conn, addr):
    """Lida com a conexão de um coletor."""
    print(f"Conexão recebida de {addr}")
    try:
        dados_recebidos = b''
        while True:
            parte = conn.recv(1024)
            if not parte:
                break
            dados_recebidos += parte
        
        if dados_recebidos:
            dados = json.loads(dados_recebidos.decode('utf-8'))
            print(f"Dados deserializados: {dados}")
            salvar_dados(dados)

    except Exception as e:
        print(f"Erro ao manipular cliente {addr}: {e}")
    finally:
        conn.close()
        print(f"Conexão com {addr} fechada.")

def iniciar_servidor_tcp():
    """Inicia o servidor TCP para escutar os coletores."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST_TCP, PORTA_TCP))
        s.listen()
        print(f"Servidor TCP escutando em {HOST_TCP}:{PORTA_TCP}")
        while True:
            conn, addr = s.accept()
            # Cria uma nova thread para cada cliente, para não bloquear o servidor
            thread_cliente = threading.Thread(target=manipular_cliente, args=(conn, addr))
            thread_cliente.start()

# --- LÓGICA DA API REST (FLASK) ---
app = Flask(__name__)
CORS(app) # Permite que o frontend (HTML/JS) acesse a API

@app.route('/maquinas', methods=['GET'])
def get_maquinas():
    """Retorna a lista de todas as máquinas e suas últimas métricas."""
    conn = conectar_db()
    cursor = conn.cursor(dictionary=True)
    
    # Query para pegar os dados estáticos e a última métrica de cada máquina
    sql = """
    SELECT m.*, met.cpu_uso_percent, met.ram_uso_percent, met.disco_uso_percent, met.temperatura_cpu, met.timestamp
    FROM maquinas m
    LEFT JOIN (
        SELECT id_maquina, cpu_uso_percent, ram_uso_percent, disco_uso_percent, temperatura_cpu, timestamp,
        ROW_NUMBER() OVER(PARTITION BY id_maquina ORDER BY timestamp DESC) as rn
        FROM metricas
    ) as met ON m.id_maquina = met.id_maquina AND met.rn = 1
    ORDER BY m.ultimo_contato DESC;
    """
    cursor.execute(sql)
    resultado = cursor.fetchall()
    
    cursor.close()
    conn.close()
    return jsonify(resultado)

@app.route('/alertas', methods=['GET'])
def get_alertas():
    """Retorna os 20 alertas mais recentes."""
    conn = conectar_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM alertas ORDER BY timestamp DESC LIMIT 20")
    resultado = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(resultado)

def iniciar_api_rest():
    """Inicia a API Flask."""
    app.run(host=HOST_API, port=PORTA_API, debug=False)

# --- INICIALIZAÇÃO ---
if __name__ == "__main__":
    # Inicia o servidor TCP em uma thread separada
    thread_tcp = threading.Thread(target=iniciar_servidor_tcp)
    thread_tcp.daemon = True # Permite que o programa principal feche mesmo com a thread rodando
    thread_tcp.start()
    
    # Inicia a API REST na thread principal
    print(f"Servidor da API REST rodando em http://{HOST_API}:{PORTA_API}")
    iniciar_api_rest()