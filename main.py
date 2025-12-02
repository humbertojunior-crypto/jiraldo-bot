import os
import time
import json
import requests
import schedule
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from threading import Thread

app = Flask(__name__)

# ========================================
# ⚙️ CONFIGURAÇÕES
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

def get_user_tickets(email):
    """Busca tickets de um usuário"""
    try:
        jql_query = f'assignee = "{email}" AND status != Done ORDER BY priority DESC, created DESC'
        
        url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
        payload = {
            "jql": jql_query,
            "fields": ["key", "summary", "status", "priority", "assignee", "created", "duedate"],
            "maxResults": 20
        }
        
        response = requests.post(url, headers=get_jira_headers(), json=payload, timeout=30)
        
        if response.status_code == 200:
            return response.json().get("issues", [])
        return []
        
    except Exception as e:
        print(f"❌ Erro buscar tickets: {e}")
        return []

def get_team_summary():
    """Relatório resumido da equipe"""
    try:
        # Buscar todos os tickets em aberto da equipe
        jql_query = 'status != Done AND assignee is not EMPTY ORDER BY assignee'
        
        url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
        payload = {
            "jql": jql_query,
            "fields": ["assignee", "status", "priority"],
            "maxResults": 200
        }
        
        response = requests.post(url, headers=get_jira_headers(), json=payload, timeout=30)
        
        if response.status_code == 200:
            issues = response.json().get("issues", [])
            
            # Agrupar por assignee
            team_stats = {}
            for issue in issues:
                assignee = issue["fields"]["assignee"]["emailAddress"]
                name = issue["fields"]["assignee"]["displayName"]
                status = issue["fields"]["status"]["name"]
                
                if assignee not in team_stats:
                    team_stats[assignee] = {
                        "name": name,
                        "total": 0,
                        "em_progresso": 0,
                        "a_fazer": 0,
                        "bloqueado": 0
                    }
                
                team_stats[assignee]["total"] += 1
                
                if "progress" in status.lower() or "doing" in status.lower():
                    team_stats[assignee]["em_progresso"] += 1
                elif "do" in status.lower() or "fazer" in status.lower():
                    team_stats[assignee]["a_fazer"] += 1
                elif "block" in status.lower() or "bloque" in status.lower():
                    team_stats[assignee]["bloqueado"] += 1
            
            return team_stats
        return {}
        
    except Exception as e:
        print(f"❌ Erro relatório equipe: {e}")
        return {}

def get_upcoming_deadlines():
    """Busca deadlines próximos"""
    try:
        # Issues com due date nos próximos 7 dias
        jql_query = 'duedate >= now() AND duedate <= "7d" AND status != Done ORDER BY duedate ASC'
        
        url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
        payload = {
            "jql": jql_query,
            "fields": ["key", "summary", "duedate", "assignee", "priority"],
            "maxResults": 20
        }
        
        response = requests.post(url, headers=get_jira_headers(), json=payload, timeout=30)
        
        if response.status_code == 200:
            return response.json().get("issues", [])
        return []
        
    except Exception as e:
        print(f"❌ Erro deadlines: {e}")
        return []

def get_recent_assignments():
    """Busca atribuições recentes no Jira - para notificações automáticas"""
    try:
        jql_query = "assignee changed during (-3m, now()) AND assignee is not EMPTY"
        
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
            print(f"❌ Erro Jira Recent: {response.status_code}")
            return []
            
    except Exception as e:
        print(f"❌ Erro ao consultar atribuições: {e}")
        return []

# ========================================
# 📱 FUNÇÕES SLACK
# ========================================

def get_slack_user_by_mention(user_id):
    """Busca informações do usuário Slack"""
    try:
        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        
        response = requests.get(
            f"https://slack.com/api/users.info",
            headers=headers,
            params={"user": user_id}
        )
        
        if response.json().get("ok"):
            return response.json()["user"]
        return None
        
    except Exception as e:
        print(f"❌ Erro buscar usuário: {e}")
        return None

def send_channel_message(channel_id, message, thread_ts=None):
    """Envia mensagem pública no canal"""
    try:
        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        
        payload = {
            "channel": channel_id,
            "text": message,
            "username": "Jiraldo",
            "icon_emoji": ":robot_face:",
            "parse": "none"
        }
        
        if thread_ts:
            payload["thread_ts"] = thread_ts
        
        response = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers=headers,
            json=payload
        )
        
        return response.json().get("ok", False)
        
    except Exception as e:
        print(f"❌ Erro enviar mensagem: {e}")
        return False

def send_slack_dm(user_email, message, attachments=None):
    """Envia DM para usuário no Slack - para notificações automáticas"""
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
    """Envia notificação DM de atribuição"""
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
# 🧠 PROCESSAMENTO LINGUAGEM NATURAL
# ========================================

def process_natural_question(text, user_id, channel_id):
    """Processa pergunta em linguagem natural"""
    
    text_lower = text.lower()
    
    # Remover menção ao bot
    text_clean = re.sub(r'<@[A-Z0-9]+>', '', text_lower).strip()
    
    # Buscar informações do usuário
    slack_user = get_slack_user_by_mention(user_id)
    if not slack_user:
        return "❌ Não consegui identificar seu usuário."
    
    user_email = slack_user.get("profile", {}).get("email", "")
    if not user_email:
        # Tentar construir email pelo nome de usuário
        username = slack_user.get("name", "")
        user_email = username + EMAIL_DOMAIN
    
    display_name = slack_user.get("real_name", slack_user.get("name", ""))
    
    # ========== COMANDOS PESSOAIS ==========
    
    if any(word in text_clean for word in ["meus tickets", "tickets", "minhas tarefas"]):
        tickets = get_user_tickets(user_email)
        
        if not tickets:
            return f"🎉 @{display_name}, você não tem tickets em aberto!"
        
        response = f"🎯 @{display_name}, você tem {len(tickets)} ticket(s) em aberto:\n"
        for i, ticket in enumerate(tickets[:5], 1):
            key = ticket["key"]
            summary = ticket["fields"]["summary"]
            status = ticket["fields"]["status"]["name"]
            priority = ticket["fields"]["priority"]["name"]
            
            emoji = "🔥" if "high" in priority.lower() or "urgent" in priority.lower() else "📝"
            response += f"{emoji} *{key}*: {summary} _({status})_\n"
        
        if len(tickets) > 5:
            response += f"\n... e mais {len(tickets) - 5} tickets"
            
        return response
    
    # ========== COMANDOS DA EQUIPE ==========
    
    elif any(word in text_clean for word in ["relatório", "equipe", "time", "team"]):
        team_stats = get_team_summary()
        
        if not team_stats:
            return "❌ Não consegui gerar relatório da equipe."
        
        response = "📊 *Relatório da Equipe:*\n"
        for email, stats in sorted(team_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:10]:
            name = stats["name"].split()[0]  # Primeiro nome
            total = stats["total"]
            em_progresso = stats["em_progresso"]
            bloqueado = stats["bloqueado"]
            
            status_emoji = "🚨" if bloqueado > 0 else "🔥" if em_progresso > 2 else "✅"
            response += f"{status_emoji} *{name}*: {total} tickets"
            
            if em_progresso > 0:
                response += f" ({em_progresso} em progresso)"
            if bloqueado > 0:
                response += f" ⚠️ {bloqueado} bloqueado(s)"
            
            response += "\n"
        
        return response
    
    elif any(word in text_clean for word in ["deadline", "prazo", "vencimento", "entrega"]):
        deadlines = get_upcoming_deadlines()
        
        if not deadlines:
            return "🎉 Não há deadlines próximos nos próximos 7 dias!"
        
        response = "⏰ *Deadlines Próximos:*\n"
        for deadline in deadlines[:10]:
            key = deadline["key"]
            summary = deadline["fields"]["summary"][:50]
            due_date = deadline["fields"]["duedate"]
            assignee_name = deadline["fields"]["assignee"]["displayName"].split()[0]
            
            # Calcular dias restantes
            due_datetime = datetime.strptime(due_date, "%Y-%m-%d")
            days_left = (due_datetime - datetime.now()).days
            
            urgency = "🚨" if days_left <= 1 else "⚠️" if days_left <= 3 else "📅"
            response += f"{urgency} *{key}*: {summary}... - {assignee_name} ({days_left} dias)\n"
        
        return response
    
    # ========== COMANDOS GERAIS ==========
    
    elif any(word in text_clean for word in ["help", "ajuda", "comandos"]):
        return """🤖 *Comandos do Jiraldo:*

*👤 Pessoais:*
• "meus tickets" - Ver seus tickets
• "minhas tarefas" - Mesma coisa

*👥 Equipe:*  
• "relatório da equipe" - Status do time
• "deadlines próximos" - Prazos importantes

*💡 Exemplos:*
• @Jiraldo meus tickets
• @Jiraldo relatório da equipe
• @Jiraldo deadlines próximos"""
    
    # ========== DEFAULT ==========
    else:
        return f"""🤔 @{display_name}, não entendi sua pergunta. 

Tente:
• "meus tickets" 
• "relatório da equipe"
• "deadlines próximos"
• "help" para ver todos os comandos"""

# ========================================
# 🕐 MONITORAMENTO AUTOMÁTICO (DMs)
# ========================================

def check_new_assignments():
    """Verifica novas atribuições para DMs automáticas"""
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
    
    print(f"🚀 Monitoramento DM iniciado (a cada {CHECK_INTERVAL_MINUTES}min)")
    
    while True:
        schedule.run_pending()
        time.sleep(30)

# ========================================
# 🎯 EVENTOS SLACK (CANAL PÚBLICO)
# ========================================

@app.route("/events", methods=["POST"])
def slack_events():
    """Processa eventos do Slack"""
    
    data = request.json
    
    # Verificação inicial do Slack
    if "challenge" in data:
        print("🔍 Challenge recebido do Slack")
        return data["challenge"]
    
    # Verificar se é um evento válido
    event = data.get("event", {})
    event_type = event.get("type")
    
    if event_type == "message":
        # Ignorar mensagens do próprio bot
        if event.get("bot_id"):
            return "ok"
            
        channel_id = event.get("channel")
        user_id = event.get("user")
        text = event.get("text", "")
        thread_ts = event.get("thread_ts", event.get("ts"))
        
        # Verificar se o bot foi mencionado
        if "jiraldo" in text.lower() or f"<@{get_bot_user_id()}>" in text:
            
            # Log da interação
            print(f"🗨️ Mensagem recebida: Canal {channel_id}, User {user_id}: {text}")
            
            # Processar pergunta
            response = process_natural_question(text, user_id, channel_id)
            
            # Enviar resposta
            success = send_channel_message(channel_id, response, thread_ts)
            
            if success:
                print(f"✅ Resposta enviada para canal {channel_id}")
            else:
                print(f"❌ Erro ao enviar resposta para canal {channel_id}")
    
    return "ok"

def get_bot_user_id():
    """Obtém ID do bot"""
    try:
        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        response = requests.get("https://slack.com/api/auth.test", headers=headers)
        if response.json().get("ok"):
            return response.json()["user_id"]
    except:
        pass
    return None

# ========================================
# 🔍 ENDPOINTS DEBUG + COMPATIBILIDADE
# ========================================

@app.route("/debug", methods=["GET"])
def debug_info():
    """Debug endpoint"""
    return jsonify({
        "timestamp": datetime.now().isoformat(),
        "bot_status": "🤖 Canal Público + DMs Automáticas",
        "features": ["event_subscriptions", "natural_language", "team_reports", "auto_notifications"],
        "environment_check": {
            "SLACK_BOT_TOKEN": "✅ Configurado" if SLACK_BOT_TOKEN else "❌ Faltando",
            "JIRA_EMAIL": JIRA_EMAIL if JIRA_EMAIL else "❌ Faltando", 
            "JIRA_API_TOKEN": "✅ Configurado" if JIRA_API_TOKEN else "❌ Faltando",
            "JIRA_BASE_URL": JIRA_BASE_URL,
            "EMAIL_DOMAIN": EMAIL_DOMAIN
        }
    })

@app.route("/test-user/<username>", methods=["GET"])
def test_user_tickets(username):
    """Compatibilidade - testa busca de tickets"""
    user_email = username + EMAIL_DOMAIN
    tickets = get_user_tickets(user_email)
    
    return jsonify({
        "api_version": "v3/search/jql (CANAL PÚBLICO)",
        "user_email": user_email,
        "total_found": len(tickets),
        "tickets": tickets[:3],  # Só os primeiros 3 para não poluir
        "note": "Use @Jiraldo meus tickets no canal público!"
    })

# Compatibilidade com slash commands
@app.route("/jiraldo", methods=["POST"])
def jiraldo_command():
    """Compatibilidade com comandos slash"""
    return jsonify({
        "response_type": "ephemeral",
        "text": "🎉 Jiraldo agora funciona em canal público! \nVá para #jiraldo e digite: @Jiraldo meus tickets"
    })

@app.route("/health", methods=["GET"])
def health():
    """Health check"""
    return {"status": "ok", "mode": "canal_publico", "endpoint_events": "/events", "timestamp": datetime.now().isoformat()}

@app.route("/", methods=["GET"])
def home():
    """Home"""
    return {
        "message": "🤖 Jiraldo Canal Público Online!", 
        "version": "2.0",
        "features": "Canal público + DMs automáticas",
        "endpoints": {
            "events": "/events",
            "debug": "/debug", 
            "health": "/health"
        }
    }

# ========================================
# 🚀 INICIALIZAÇÃO
# ========================================

if __name__ == "__main__":
    print("🤖 Jiraldo CANAL PÚBLICO + DMs iniciando...")
    print("💬 Modo: Event Subscriptions + Linguagem Natural")
    print("🔔 DMs automáticas: ATIVO")
    print("🔗 Endpoint eventos: /events")
    print(f"🚪 Porta: {PORT}")
    
    # Teste conexão se tokens configurados
    if all([SLACK_BOT_TOKEN, JIRA_EMAIL, JIRA_API_TOKEN]):
        # Iniciar monitoramento DM em thread separada
        monitor_thread = Thread(target=start_monitoring, daemon=True)
        monitor_thread.start()
        print("✅ Monitoramento DM ativo!")
    
    print("🌐 Servidor iniciando...")
    app.run(host="0.0.0.0", port=PORT, debug=False)
