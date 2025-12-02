import os
import time
import json
import requests
import schedule
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from threading import Thread

app = Flask(__name__)

# ========================================
# ⚙️ CONFIGURAÇÕES SEGURAS
# ========================================

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "https://ifood.atlassian.net")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")

CHECK_INTERVAL_MINUTES = 2
NOTIFICATION_HOURS = (8, 18)
EMAIL_DOMAIN = "@ifood.com.br"
PORT = int(os.getenv("PORT", 5000))

if not all([SLACK_BOT_TOKEN, JIRA_EMAIL, JIRA_API_TOKEN]):
    print("❌ CONFIGURE AS VARIÁVEIS DE AMBIENTE NO RENDER:")
    print("   SLACK_BOT_TOKEN")
    print("   JIRA_EMAIL") 
    print("   JIRA_API_TOKEN")

# ========================================
# 🔧 FUNÇÕES JIRA (API CORRIGIDA)
# ========================================

def get_jira_headers():
    """Headers para autenticação Jira"""
    import base64
    credentials = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode()).decode()
    return {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

def get_recent_assignments():
    """Busca atribuições recentes no Jira - API CORRIGIDA"""
    try:
        jql_query = "assignee changed during (-3m, now()) AND assignee is not EMPTY"
        
        # ✅ NOVA API CORRETA
        url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
        
        payload = {
            "jql": jql_query,
            "fields": ["key", "summary", "assignee", "status", "priority", "creator", "updated"],
            "maxResults": 50
        }
        
        response = requests.post(url, headers=get_jira_headers(), json=payload, timeout=30)
        
        if response.status_code == 200:
            return response.json().get("issues", [])
        else:
            print(f"❌ Erro Jira Recent: {response.status_code} - {response.text}")
            return []
            
    except Exception as e:
        print(f"❌ Erro ao consultar atribuições: {e}")
        return []

def get_user_tickets(email):
    """Busca tickets de um usuário específico - API CORRIGIDA"""
    try:
        jql_query = f'assignee = "{email}" AND status != Done ORDER BY created DESC'
        
        # ✅ NOVA API CORRETA
        url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
        
        payload = {
            "jql": jql_query,
            "fields": ["key", "summary", "status", "priority", "assignee", "created"],
            "maxResults": 10
        }
        
        response = requests.post(url, headers=get_jira_headers(), json=payload, timeout=30)
        
        if response.status_code == 200:
            return response.json().get("issues", [])
        else:
            print(f"❌ Erro Jira User: {response.status_code} - {response.text}")
            return []
        
    except Exception as e:
        print(f"❌ Erro ao buscar tickets: {e}")
        return []

# ========================================
# 📱 FUNÇÕES SLACK
# ========================================

def send_slack_dm(user_email, message, attachments=None):
    """Envia DM para usuário no Slack"""
    try:
        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        
        user_response = requests.get(
            "https://slack.com/api/users.lookupByEmail",
            headers=headers,
            params={"email": user_email},
            timeout=30
        )
        
        if user_response.json().get("ok"):
            user_id = user_response.json()["user"]["id"]
            
            payload = {
                "channel": user_id,
                "text": message,
                "username": "Jiraldo",
                "icon_emoji": ":robot_face:"
            }
            
            if attachments:
                payload["attachments"] = attachments
            
            dm_response = requests.post(
                "https://slack.com/api/chat.postMessage",
                headers=headers,
                json=payload,
                timeout=30
            )
            
            return dm_response.json().get("ok", False)
        else:
            print(f"❌ Usuário não encontrado: {user_email}")
            return False
        
    except Exception as e:
        print(f"❌ Erro ao enviar DM: {e}")
        return False

def send_slack_notification(assignment):
    """Envia notificação de atribuição"""
    try:
        assignee_email = assignment["fields"]["assignee"]["emailAddress"]
        ticket_key = assignment["key"]
        ticket_summary = assignment["fields"]["summary"]
        priority = assignment["fields"]["priority"]["name"]
        ticket_url = f"{JIRA_BASE_URL}/browse/{ticket_key}"
        
        message = f"🎯 Novo ticket atribuído para você!"
        
        attachments = [{
            "color": "good",
            "fields": [
                {"title": "Ticket", "value": ticket_key, "short": True},
                {"title": "Prioridade", "value": priority, "short": True},
                {"title": "Título", "value": ticket_summary, "short": False}
            ],
            "actions": [{
                "type": "button",
                "text": "🔗 Abrir no Jira",
                "url": ticket_url
            }],
            "footer": "Jiraldo Bot",
            "ts": time.time()
        }]
        
        success = send_slack_dm(assignee_email, message, attachments)
        
        if success:
            print(f"✅ Notificação enviada para {assignee_email}")
        else:
            print(f"❌ Falha ao notificar {assignee_email}")
            
    except Exception as e:
        print(f"❌ Erro na notificação: {e}")

# ========================================
# 🕐 MONITORAMENTO AUTOMÁTICO
# ========================================

def check_new_assignments():
    """Verifica novas atribuições"""
    try:
        current_hour = datetime.now().hour
        if current_hour < NOTIFICATION_HOURS[0] or current_hour > NOTIFICATION_HOURS[1]:
            return
        
        print("🔍 Verificando novas atribuições...")
        assignments = get_recent_assignments()
        
        for assignment in assignments:
            if assignment["fields"].get("assignee"):
                send_slack_notification(assignment)
        
        if assignments:
            print(f"📋 Processadas {len(assignments)} atribuições")
        
    except Exception as e:
        print(f"❌ Erro no monitoramento: {e}")

def start_monitoring():
    """Inicia monitoramento automático"""
    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(check_new_assignments)
    
    print(f"🚀 Monitoramento iniciado (a cada {CHECK_INTERVAL_MINUTES}min)")
    
    while True:
        schedule.run_pending()
        time.sleep(30)

# ========================================
# 🔍 ENDPOINTS DEBUG (API CORRIGIDA)
# ========================================

@app.route("/debug", methods=["GET"])
def debug_info():
    """Debug - mostra configurações e testa conexão"""
    
    debug_data = {
        "timestamp": datetime.now().isoformat(),
        "bot_status": "online",
        "api_version": "v3/search/jql (CORRIGIDA)",
        "environment_check": {
            "SLACK_BOT_TOKEN": "✅ Configurado" if SLACK_BOT_TOKEN else "❌ Faltando",
            "JIRA_EMAIL": JIRA_EMAIL if JIRA_EMAIL else "❌ Faltando", 
            "JIRA_API_TOKEN": "✅ Configurado" if JIRA_API_TOKEN else "❌ Faltando",
            "JIRA_BASE_URL": JIRA_BASE_URL,
            "EMAIL_DOMAIN": EMAIL_DOMAIN
        }
    }
    
    # Teste conexão Jira
    if all([JIRA_EMAIL, JIRA_API_TOKEN]):
        try:
            headers = get_jira_headers()
            response = requests.get(f"{JIRA_BASE_URL}/rest/api/3/myself", headers=headers, timeout=10)
            
            if response.status_code == 200:
                user_data = response.json()
                debug_data["jira_connection"] = {
                    "status": "✅ Conectado",
                    "user": user_data.get("displayName", "N/A"),
                    "email": user_data.get("emailAddress", "N/A"),
                    "account_id": user_data.get("accountId", "N/A")[:20] + "..."
                }
            else:
                debug_data["jira_connection"] = {
                    "status": "❌ Erro",
                    "code": response.status_code,
                    "message": response.text[:200]
                }
        except Exception as e:
            debug_data["jira_connection"] = {
                "status": "❌ Erro de conexão", 
                "error": str(e)
            }
    else:
        debug_data["jira_connection"] = {"status": "❌ Tokens não configurados"}
    
    return jsonify(debug_data)

@app.route("/test-user/<username>", methods=["GET"])
def test_user_tickets(username):
    """Testa busca de tickets para usuário - API CORRIGIDA"""
    
    if not all([JIRA_EMAIL, JIRA_API_TOKEN]):
        return jsonify({"error": "Environment variables não configuradas"})
    
    user_email = username + EMAIL_DOMAIN
    
    try:
        jql_query = f'assignee = "{user_email}" AND status != Done ORDER BY created DESC'
        
        # ✅ NOVA API CORRETA
        url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
        
        payload = {
            "jql": jql_query,
            "fields": ["key", "summary", "status", "priority", "assignee", "created"],
            "maxResults": 10
        }
        
        headers = get_jira_headers()
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            tickets = []
            
            for issue in data.get("issues", []):
                tickets.append({
                    "key": issue["key"],
                    "summary": issue["fields"]["summary"],
                    "status": issue["fields"]["status"]["name"],
                    "assignee": issue["fields"]["assignee"]["emailAddress"] if issue["fields"]["assignee"] else None,
                    "created": issue["fields"]["created"]
                })
            
            return jsonify({
                "api_version": "v3/search/jql (CORRIGIDA)",
                "user_email": user_email,
                "jql_query": jql_query,
                "total_found": len(tickets),
                "tickets": tickets,
                "note": "Bot só mostra tickets onde você é ASSIGNEE (não REPORTER)"
            })
        else:
            return jsonify({
                "error": f"Erro Jira: {response.status_code}",
                "message": response.text[:500],
                "api_version": "v3/search/jql (CORRIGIDA)"
            })
            
    except Exception as e:
        return jsonify({
            "error": "Erro na busca",
            "message": str(e),
            "api_version": "v3/search/jql (CORRIGIDA)"
        })

# ========================================
# 🎯 COMANDOS SLACK
# ========================================

@app.route("/jiraldo", methods=["POST"])
def jiraldo_command():
    """Processa comandos /jiraldo"""
    try:
        command_text = request.form.get("text", "").strip().lower()
        user_name = request.form.get("user_name", "")
        user_email = user_name + EMAIL_DOMAIN
        
        # Log para debug
        print(f"🔍 Comando: {command_text}, User: {user_name}, Email: {user_email}")
        
        if "tickets" in command_text or "meus" in command_text:
            tickets = get_user_tickets(user_email)
            
            if tickets:
                response = f"🎯 Seus tickets em aberto ({len(tickets)}):\n"
                for ticket in tickets[:5]:
                    key = ticket["key"]
                    summary = ticket["fields"]["summary"]
                    status = ticket["fields"]["status"]["name"]
                    response += f"• *{key}*: {summary} _({status})_\n"
                
                if len(tickets) > 5:
                    response += f"\n... e mais {len(tickets) - 5} tickets"
            else:
                response = "🎉 Você não tem tickets em aberto!"
            
        elif "help" in command_text:
            response = """🤖 *Comandos do Jiraldo:*
• `/jiraldo tickets` - Seus tickets em aberto
• `/jiraldo help` - Esta ajuda

*Notificações automáticas:*
• Você será notificado quando receber novos tickets!

*Debug:*
• jiraldo-bot.onrender.com/debug"""
        
        else:
            response = "🤔 Comando não reconhecido. Digite `/jiraldo help`"
        
        return jsonify({
            "response_type": "ephemeral",
            "text": response
        })
        
    except Exception as e:
        return jsonify({
            "response_type": "ephemeral",
            "text": f"❌ Erro: {e}"
        })

@app.route("/health", methods=["GET"])
def health():
    """Endpoint de saúde"""
    return {"status": "ok", "jiraldo": "online", "api_fixed": "v3/search/jql", "timestamp": datetime.now().isoformat()}

@app.route("/", methods=["GET"])
def home():
    """Página inicial"""
    return {"message": "🤖 Jiraldo Bot Online!", "status": "running", "debug": "/debug", "api_fixed": "✅"}

# ========================================
# 🚀 INICIALIZAÇÃO
# ========================================

if __name__ == "__main__":
    print("🤖 Jiraldo Bot CORRIGIDO iniciando...")
    print("✅ API Jira atualizada para v3/search/jql")
    print("🔐 Usando variáveis de ambiente para tokens")
    print(f"🌐 Jira: {JIRA_BASE_URL}")
    print(f"📧 Domain: {EMAIL_DOMAIN}")
    print(f"🚪 Porta: {PORT}")
    
    # Teste conexão se tokens estão configurados
    if all([SLACK_BOT_TOKEN, JIRA_EMAIL, JIRA_API_TOKEN]):
        try:
            headers = get_jira_headers()
            test_response = requests.get(f"{JIRA_BASE_URL}/rest/api/3/myself", headers=headers, timeout=10)
            
            if test_response.status_code == 200:
                user_info = test_response.json()
                print(f"✅ Jira OK! Usuário: {user_info.get('displayName', 'N/A')}")
            else:
                print(f"❌ Erro Jira: {test_response.status_code}")
        except Exception as e:
            print(f"⚠️ Aviso Jira: {e}")
        
        # Iniciar monitoramento
        monitor_thread = Thread(target=start_monitoring, daemon=True)
        monitor_thread.start()
    
    # Iniciar servidor
    print("🌐 Servidor iniciando...")
    print("🔍 Debug disponível em: /debug")
    app.run(host="0.0.0.0", port=PORT, debug=False)
