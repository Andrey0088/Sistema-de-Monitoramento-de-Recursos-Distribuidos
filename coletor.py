import psutil
import socket
import json
import time
import uuid

# --- CONFIGURAÇÕES ---
# Endereço IP e porta do Agregador Central
AGREGADOR_HOST = '127.0.0.1'  # Mudar para o IP do seu servidor agregador !!!!
AGREGADOR_PORTA = 9999

# Intervalo entre os envios de dados (em segundos)
INTERVALO_COLETA = 5 

# Identificador único para esta máquina
# Pega o endereço MAC para usar como um ID mais estável
ID_MAQUINA = hex(uuid.getnode())

def coletar_dados_estaticos():
    """Coleta informações que não mudam com frequência."""
    try:
        info_cpu = {
            "modelo": psutil.cpu_freq().max,
            "nucleos_fisicos": psutil.cpu_count(logical=False),
            "nucleos_logicos": psutil.cpu_count(logical=True)
        }
        memoria_total = psutil.virtual_memory().total / (1024**3) # Em GB
        disco_total = psutil.disk_usage('/').total / (1024**3) # Em GB

        return {
            "tipo": "estatico",
            "id_maquina": ID_MAQUINA,
            "info_cpu": info_cpu,
            "memoria_total_gb": round(memoria_total, 2),
            "disco_total_gb": round(disco_total, 2)
        }
    except Exception as e:
        print(f"Erro ao coletar dados estáticos: {e}")
        return None

def coletar_dados_dinamicos():
    """Coleta métricas que mudam constantemente."""
    try:
        # Tenta coletar a temperatura
        temp_cpu = 0
        try:
            # Esta parte pode falhar em alguns sistemas
            temp_cpu = psutil.sensors_temperatures().get('coretemp', [{}])[0].current or 0
        except (AttributeError, KeyError, IndexError):
            # Se falhar, apenas ignora e mantém a temperatura como 0
            print("Não foi possível ler a temperatura do sistema.")
            pass

        return {
            "tipo": "dinamico",
            "id_maquina": ID_MAQUINA,
            "cpu_uso_percent": psutil.cpu_percent(interval=1),
            "ram_uso_percent": psutil.virtual_memory().percent,
            "disco_uso_percent": psutil.disk_usage('/').percent,
            "temperatura_cpu": temp_cpu
        }
    except Exception as e:
        print(f"Erro ao coletar dados dinâmicos: {e}")
        return None

def enviar_dados(dados):
    """Envia um dicionário de dados para o Agregador Central via TCP."""
    try:
        # Cria um socket TCP/IP 
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((AGREGADOR_HOST, AGREGADOR_PORTA))
            
            # Serializa os dados para JSON e depois para bytes 
            mensagem = json.dumps(dados).encode('utf-8')
            
            s.sendall(mensagem)
            print(f"Dados enviados com sucesso: {dados['tipo']}")
            
    except ConnectionRefusedError:
        print("Erro: A conexão foi recusada. O Agregador Central está online?")
    except Exception as e:
        print(f"Ocorreu um erro ao enviar os dados: {e}")

if __name__ == "__main__":
    print("Iniciando Coletor de Recursos...")
    print(f"ID desta máquina: {ID_MAQUINA}")
    
    # Envia os dados estáticos uma vez ao iniciar
    dados_estaticos = coletar_dados_estaticos()
    if dados_estaticos:
        enviar_dados(dados_estaticos)

    # Inicia o loop de coleta e envio de dados dinâmicos
    while True:
        dados_dinamicos = coletar_dados_dinamicos()
        if dados_dinamicos:
            enviar_dados(dados_dinamicos)
        
        time.sleep(INTERVALO_COLETA)