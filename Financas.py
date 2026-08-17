import streamlit as st
import pandas as pd
import pdfplumber
import re
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
import plotly.express as px
import openai
import os

# === 🔑 SEGREDOS DO APLICATIVO ===
GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
DATABASE_URL = st.secrets["DATABASE_URL"]
# =======================================

st.set_page_config(page_title="Controle Financeiro Inteligente", layout="wide", page_icon="💸")

# ==========================================
# 1. BANCO DE DADOS E CARGA AUTOMÁTICA
# ==========================================
engine = create_engine(DATABASE_URL)

def inicializar_banco():
    with engine.begin() as conn:
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS historico (
                id SERIAL PRIMARY KEY,
                transacao_id INT,
                data TEXT,
                competencia TEXT,
                mes_ano TEXT,
                tipo TEXT,
                categoria TEXT,
                natureza TEXT,
                descricao TEXT,
                via_pgto TEXT,
                pagador_recebedor TEXT,
                titular TEXT,
                parcela TEXT,
                valor REAL,
                status TEXT,
                c_despesa REAL,
                c_guardar REAL,
                c_gastar_b REAL,
                c_gastar_c REAL,
                usuario TEXT
            )
        '''))
        
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS usuarios (
                usuario TEXT PRIMARY KEY,
                senha TEXT
            )
        '''))
        
        res_user = conn.execute(text("SELECT COUNT(*) FROM usuarios")).fetchone()
        if res_user[0] == 0:
            conn.execute(text("INSERT INTO usuarios (usuario, senha) VALUES ('Christian', '1234')"))

        # CARGA AUTOMÁTICA DA PLANILHA SE A TABELA ESTIVER VAZIA
        res_hist = conn.execute(text("SELECT COUNT(*) FROM historico")).fetchone()
        if res_hist[0] == 0 and os.path.exists('Controle Financeiro.xlsx'):
            try:
                df_excel = pd.read_excel('Controle Financeiro.xlsx', sheet_name='Movimentações', header=1)
                df_banco = df_excel.copy()
                df_banco['usuario'] = 'Christian'
                
                renomear = {
                    'ID': 'transacao_id',
                    'Data': 'data',
                    'Competência': 'competencia',
                    'Tipo': 'tipo',
                    'Categoria': 'categoria',
                    'Natureza': 'natureza',
                    'Descrição': 'descricao',
                    'Via de Pgto': 'via_pgto',
                    'Pagador/Recebedor': 'pagador_recebedor',
                    'Titular': 'titular',
                    'Parcela': 'parcela',
                    'Valor': 'valor',
                    'Status': 'status',
                    'C.Despesa': 'c_despesa',
                    'C.Guardar': 'c_guardar',
                    'C.Gastar B': 'c_gastar_b',
                    'C.Gastar C': 'c_gastar_c'
                }
                df_banco = df_banco.rename(columns=renomear)
                df_banco['data'] = pd.to_datetime(df_banco['data'], errors='coerce').dt.strftime('%d/%m/%Y')
                df_banco['competencia'] = pd.to_datetime(df_banco['competencia'], errors='coerce').dt.strftime('%d/%m/%Y')
                df_banco['mes_ano'] = pd.to_datetime(df_banco['competencia'], format='%d/%m/%Y', errors='coerce').dt.strftime('%m/%Y')
                df_banco['parcela'] = df_banco['parcela'].astype(str).replace('nan', None)
                
                df_banco.to_sql('historico', conn, if_exists='append', index=False)
            except Exception as e:
                print(f"Erro na carga automática: {e}")

inicializar_banco()

def formata_br(valor):
    if pd.isna(valor): return "R$ 0,00"
    return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ==========================================
# 2. APLICATIVO PRINCIPAL
# ==========================================
def tela_principal():
    usuario_atual = st.session_state['usuario']
    
    st.sidebar.title("Configurações")
    st.sidebar.write(f"Bem-vindo, **{usuario_atual}**!")
    
    if st.sidebar.button("Sair (Logout)"):
        st.session_state['autenticado'] = False
        st.rerun()

    st.title("💸 Controle Financeiro Inteligente")
    
    aba_lancamento, aba_historico, aba_dashboard, aba_ia = st.tabs([
        "➕ Nova Transação", "🗄️ Histórico & Edição", "📈 Dashboard", "🤖 Conselheiro IA"
    ])

    with aba_lancamento:
        st.header("➕ Adicionar Nova Transação")
        st.write("Insira os dados da movimentação diretamente no sistema, sem precisar de planilhas.")
        
        with st.form("form_nova_transacao", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                tipo_t = st.selectbox("Tipo", ["Saída", "Entrada"])
                categoria_t = st.selectbox("Categoria", ["Despesa Fixa", "Despesa Variável", "Salário", "Juros"])
                natureza_t = st.selectbox("Natureza", ["Casa", "Carro", "Pedro", "Alimentação", "Mercado", "Vestuário", "Assinaturas", "Viagem", "Uber", "Salário", "Outros"])
            with col2:
                descricao_t = st.text_input("Descrição")
                valor_t = st.number_input("Valor (R$)", min_value=0.0, format="%.2f")
                via_t = st.selectbox("Via de Pagamento", ["Pix", "Crédito", "Débito", "Depósito", "Dinheiro"])
            with col3:
                titular_t = st.selectbox("Titular", ["Christian", "Bruna", "Dividido"])
                status_t = st.selectbox("Status", ["Pendente", "Pago", "Recebido"])
                data_t = st.date_input("Data do Lançamento")
            
            submitted = st.form_submit_button("Salvar Transação na Nuvem", use_container_width=True)
            if submitted:
                if descricao_t and valor_t > 0:
                    data_str = data_t.strftime('%d/%m/%Y')
                    mes_ano_str = data_t.strftime('%m/%Y')
                    
                    # Cálculo automático das alocações padrão 50/30/20 se for saída
                    val_calc = -valor_t if tipo_t == 'Saída' else valor_t
                    c_despesa = val_calc * 0.50 if tipo_t == 'Saída' else None
                    c_guardar = val_calc * 0.30 if tipo_t == 'Saída' else None
                    c_gastar_b = val_calc * 0.20 if tipo_t == 'Saída' else None
                    
                    try:
                        with engine.begin() as conn:
                            conn.execute(text('''
                                INSERT INTO historico (transacao_id, data, competencia, mes_ano, tipo, categoria, natureza, descricao, via_pgto, titular, valor, status, c_despesa, c_guardar, c_gastar_b, usuario)
                                VALUES (999, :data, :data, :mes_ano, :tipo, :cat, :nat, :desc, :via, :tit, :val, :stat, :cd, :cg, :cgb, :user)
                            '''), {
                                "data": data_str, "mes_ano": mes_ano_str, "tipo": tipo_t, "cat": categoria_t, 
                                "nat": natureza_t, "desc": descricao_t, "via": via_t, "tit": titular_t, 
                                "val": val_calc, "stat": status_t, "cd": c_despesa, "cg": c_guardar, "cgb": c_gastar_b, "user": usuario_atual
                            })
                        st.success("✅ Transação adicionada com sucesso na nuvem!")
                    except Exception as e:
                        st.error(f"Erro ao salvar: {e}")
                else:
                    st.warning("Preencha a descrição e um valor maior que zero.")

    with aba_historico:
        st.header("🗄️ Histórico Completo na Nuvem")
        st.write("Visualize, edite ou exclua seus lançamentos diretamente pelo painel.")
        try:
            with engine.connect() as conn:
                df_historico = pd.read_sql(text("SELECT * FROM historico WHERE usuario = :user ORDER BY id DESC"), conn, params={"user": usuario_atual})
            
            if not df_historico.empty:
                df_editado_db = st.data_editor(
                    df_historico, 
                    num_rows="dynamic", 
                    hide_index=True, 
                    use_container_width=True,
                    column_config={
                        "id": st.column_config.NumberColumn(disabled=True),
                        "usuario": None
                    }
                )
                
                if st.button("💾 Salvar Alterações no Banco"):
                    with engine.begin() as conn:
                        conn.execute(text("DELETE FROM historico WHERE usuario = :user"), {"user": usuario_atual})
                    df_editado_db.to_sql('historico', engine, if_exists='append', index=False)
                    st.success("Alterações gravadas permanentemente!")

                st.divider()
                st.subheader("Resumo por Categoria")
                resumo = df_historico[df_historico['tipo'] == 'Saída'].groupby('categoria')['valor'].sum().abs().reset_index()
                resumo['valor_fmt'] = resumo['valor'].apply(lambda x: formata_br(x))
                st.dataframe(resumo[['categoria', 'valor_fmt']], hide_index=True)
            else:
                st.info("Nenhum dado encontrado.")
        except Exception as e:
            st.error(f"Erro ao carregar histórico: {e}")

    with aba_dashboard:
        st.header("📈 Dashboard Analítico")
        try:
            with engine.connect() as conn:
                df_hist = pd.read_sql(text("SELECT * FROM historico WHERE usuario = :user"), conn, params={"user": usuario_atual})
            
            if not df_hist.empty:
                df_gastos = df_hist[df_hist['tipo'] == 'Saída'].copy()
                df_gastos['valor'] = df_gastos['valor'].abs()
                
                st.subheader("Gastos por Categoria e Mês")
                fig = px.bar(df_gastos, x='mes_ano', y='valor', color='categoria', barmode='group')
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Sem dados para exibir no dashboard.")
        except Exception as e:
            st.error(f"Erro no dashboard: {e}")

    with aba_ia:
        st.header("🤖 Conselheiro IA (Groq Gratuito)")
        st.write("Análise inteligente baseada nos dados salvos no banco.")
        
        if GROQ_API_KEY != "":
            if st.button("Analisar Finanças com IA", type="primary"):
                with st.spinner("Analisando dados..."):
                    try:
                        with engine.connect() as conn:
                            df_hist = pd.read_sql(text("SELECT * FROM historico WHERE usuario = :user"), conn, params={"user": usuario_atual})
                        
                        if df_hist.empty:
                            st.info("Sem dados para a IA analisar.")
                        else:
                            df_saidas = df_hist[df_hist['tipo'] == 'Saída']
                            resumo = "Gastos:\n" + "\n".join([f"- {row['categoria']} ({row['natureza']}): R$ {row['valor']}" for _, row in df_saidas.iterrows()])
                            
                            client = openai.OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
                            response = client.chat.completions.create(
                                model="llama-3.3-70b-versatile",
                                messages=[
                                    {"role": "system", "content": "Atue como consultor financeiro especialista. Responda em PT-BR."},
                                    {"role": "user", "content": f"Analise estes dados financeiros e dê 3 dicas práticas: {resumo}"}
                                ]
                            )
                            st.success("Análise concluída!")
                            st.write(response.choices[0].message.content)
                    except Exception as e:
                        st.error(f"Erro na IA: {e}")
        else:
            st.warning("Configure a chave da Groq nos secrets.")

# ==========================================
# 3. AUTENTICAÇÃO
# ==========================================
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False

if not st.session_state['autenticado']:
    st.markdown("<h1 style='text-align: center;'>🔒 Acesso ao Sistema</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("form_login"):
            usuario_login = st.text_input("Usuário")
            senha_login = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar", use_container_width=True):
                with engine.connect() as conn:
                    user = conn.execute(text("SELECT * FROM usuarios WHERE usuario = :u AND senha = :s"), {"u": usuario_login, "s": senha_login}).fetchone()
                if user:
                    st.session_state['autenticado'] = True
                    st.session_state['usuario'] = usuario_login
                    st.rerun()
                else:
                    st.error("Credenciais inválidas.")
else:
    tela_principal()
