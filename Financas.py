import streamlit as st
import pandas as pd
import pdfplumber
import re
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
import plotly.express as px
import openai

# === 🔑 SEGREDOS DO APLICATIVO ===
GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
DATABASE_URL = st.secrets["DATABASE_URL"]
# =======================================

st.set_page_config(page_title="Controle Financeiro", layout="centered", page_icon="💸")

# ==========================================
# 1. FUNÇÕES BASE E BANCO DE DADOS EM NUVEM
# ==========================================
engine = create_engine(DATABASE_URL)

def inicializar_banco():
    with engine.begin() as conn:
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS historico (
                id SERIAL PRIMARY KEY,
                data TEXT,
                mes_ano TEXT,
                descricao TEXT,
                categoria TEXT,
                valor REAL,
                tipo TEXT,
                usuario TEXT
            )
        '''))
        
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS usuarios (
                usuario TEXT PRIMARY KEY,
                senha TEXT
            )
        '''))
        
        res = conn.execute(text("SELECT COUNT(*) FROM usuarios")).fetchone()
        if res[0] == 0:
            conn.execute(text("INSERT INTO usuarios (usuario, senha) VALUES ('Christian', '1234')"))

inicializar_banco()

def extrair_dados_pdf(arquivo):
    linhas_extrato = []
    padrao = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+(.+)\s+([\-\d\.,]+)$')
    with pdfplumber.open(arquivo) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if texto:
                for linha in texto.split('\n'):
                    match = padrao.match(linha.strip())
                    if match:
                        data, lancamento, valor = match.group(1), match.group(2).strip(), match.group(3)
                        if "SALDO DO DIA" not in lancamento:
                            linhas_extrato.append({'data': data, 'origem / destino': lancamento, 'valor': valor})
    return pd.DataFrame(linhas_extrato)

def classificar_despesa(descricao):
    desc_min = str(descricao).lower()
    if any(p in desc_min for p in ['remuneracao/salario', 'somos educacao', 'pix transf christi']): return 'Ignorar'
    elif any(p in desc_min for p in ['claro', 'moura', 'oficina', 'mecânica', 'energia', 'água', 'iptu', 'ipva']): return 'Contas (50%)'
    elif any(p in desc_min for p in ['investimento', 'cdb', 'tesouro', 'cofrinho', 'guardado']): return 'Reserva (30%)'
    elif any(p in desc_min for p in ['epic game', 'steam', 'playstation', 'nintendo', 'outback', 'habib', 'boigalê', 'di paolo', 'ifood', 'ingresso']): return 'Lazer (20%)'
    return 'Outros'

def formata_br(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ==========================================
# 2. O APLICATIVO PRINCIPAL (Protegido e Isolado)
# ==========================================
def tela_principal():
    usuario_atual = st.session_state['usuario']
    
    st.sidebar.title("Configurações")
    st.sidebar.write(f"Bem-vindo, **{usuario_atual}**!")
    
    if st.sidebar.button("Sair (Logout)"):
        st.session_state['autenticado'] = False
        st.rerun()

    st.title("💸 Meu Controle Financeiro")
    
    aba_importacao, aba_historico, aba_dashboard, aba_ia = st.tabs([
        "📥 Importar", "🗄️ Banco de Dados", "📈 Dashboard", "🤖 Conselheiro IA"
    ])

    with aba_importacao:
        st.write("Faça o upload do extrato para classificar e salvar no sistema.")
        arquivo_upload = st.file_uploader("Selecione o arquivo do extrato", type=["pdf", "csv", "xlsx"])

        if arquivo_upload is not None:
            try:
                if arquivo_upload.name.endswith('.pdf'): df = extrair_dados_pdf(arquivo_upload)
                elif arquivo_upload.name.endswith('.csv'): df = pd.read_csv(arquivo_upload, sep=',') 
                else: df = pd.read_excel(arquivo_upload)
                    
                if 'valor' in df.columns:
                    df['valor_calculo'] = df['valor'].astype(str).str.replace('R$', '', regex=False).str.replace(' ', '', regex=False).str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
                    df['valor_calculo'] = pd.to_numeric(df['valor_calculo'], errors='coerce')

                if 'data' in df.columns:
                    df['data_real'] = pd.to_datetime(df['data'], format='%d/%m/%Y', errors='coerce')
                    df['mes_ano'] = df['data_real'].dt.strftime('%m/%Y')
                    st.divider()
                    mes_selecionado = st.selectbox("📅 Selecione o mês para trabalhar:", df['mes_ano'].dropna().unique())
                    df_mes = df[df['mes_ano'] == mes_selecionado].copy()
                else:
                    df_mes = df.copy()

                if 'origem / destino' in df_mes.columns and 'valor_calculo' in df_mes.columns:
                    df_mes['Categoria'] = df_mes['origem / destino'].apply(classificar_despesa)
                    
                    filtro_salario = df_mes['origem / destino'].str.lower().str.contains('remuneracao/salario|salário|salario|pagamento', na=False)
                    salario_identificado = df_mes.loc[filtro_salario, 'valor_calculo'].sum()
                    
                    if salario_identificado > 0:
                        st.header(f"🎯 Planejamento: {mes_selecionado}")
                        st.write(f"Renda identificada: **{formata_br(salario_identificado)}**")
                        painel_metricas = st.empty() 
                        
                        st.divider()
                        st.subheader("📝 Ajuste as categorias antes de salvar")
                        df_tela = df_mes[['data', 'origem / destino', 'Categoria', 'valor', 'valor_calculo', 'mes_ano']]

                        df_editado = st.data_editor(
                            df_tela,
                            column_config={"Categoria": st.column_config.SelectboxColumn(options=['Contas (50%)', 'Reserva (30%)', 'Lazer (20%)', 'Ignorar', 'Outros'], required=True),
                                           "valor_calculo": None, "mes_ano": None},
                            disabled=["data", "origem / destino", "valor"], 
                            hide_index=True, use_container_width=True
                        )

                        gastos = df_editado[df_editado['valor_calculo'] < 0]
                        real_essencial = abs(gastos[gastos['Categoria'] == 'Contas (50%)']['valor_calculo'].sum())
                        real_reserva = abs(gastos[gastos['Categoria'] == 'Reserva (30%)']['valor_calculo'].sum())
                        real_lazer = abs(gastos[gastos['Categoria'] == 'Lazer (20%)']['valor_calculo'].sum())

                        with painel_metricas.container():
                            st.subheader("Desempenho x Meta")
                            c1, c2, c3 = st.columns(3)
                            c1.metric("Contas (50%)", formata_br(real_essencial), f"{(salario_identificado * 0.50) - real_essencial:,.2f} livre")
                            c2.metric("Reserva (30%)", formata_br(real_reserva), f"{(salario_identificado * 0.30) - real_reserva:,.2f} livre")
                            c3.metric("Lazer (20%)", formata_br(real_lazer), f"{(salario_identificado * 0.20) - real_lazer:,.2f} livre")

                        st.divider()
                        st.subheader("💾 Salvar no Sistema")
                        
                        if st.button("Gravar no Banco de Dados", type="primary"):
                            df_para_banco = df_editado[df_editado['Categoria'] != 'Ignorar'].copy()
                            df_para_banco['tipo'] = df_para_banco['valor_calculo'].apply(lambda x: 'Entrada' if x > 0 else 'Saída')
                            df_para_banco['usuario'] = usuario_atual
                            
                            dados_sql = df_para_banco[['data', 'mes_ano', 'origem / destino', 'Categoria', 'valor_calculo', 'tipo', 'usuario']]
                            dados_sql.columns = ['data', 'mes_ano', 'descricao', 'categoria', 'valor', 'tipo', 'usuario']
                            
                            with engine.begin() as conn:
                                conn.execute(text("DELETE FROM historico WHERE mes_ano = :mes AND usuario = :user"), 
                                             {"mes": mes_selecionado, "user": usuario_atual})
                            
                            dados_sql.to_sql('historico', engine, if_exists='append', index=False)
                            st.success(f"Sucesso! Lançamentos de {mes_selecionado} foram gravados na nuvem. Verifique a aba 'Meu Banco de Dados'.")
                    else:
                        st.warning("Salário não encontrado neste mês.")
            except Exception as e:
                st.error(f"Erro: {e}")

    with aba_historico:
        st.header("🗄️ Histórico Completo e Edição")
        st.write("Dê dois cliques na célula para editar, ou selecione uma linha inteira e aperte `Delete` para apagá-la.")
        
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
                        "categoria": st.column_config.SelectboxColumn(options=['Contas (50%)', 'Reserva (30%)', 'Lazer (20%)', 'Outros']),
                        "usuario": None 
                    }
                )
                
                if st.button("Salvar Alterações no Banco"):
                    with engine.begin() as conn:
                        conn.execute(text("DELETE FROM historico WHERE usuario = :user"), {"user": usuario_atual})
                    df_editado_db.to_sql('historico', engine, if_exists='append', index=False)
                    st.success("Alterações gravadas permanentemente! Os gráficos já estão atualizados.")

                st.divider()
                st.subheader("Resumo por Categoria (Todo o período)")
                resumo = df_historico[df_historico['tipo'] == 'Saída'].groupby('categoria')['valor'].sum().abs().reset_index()
                resumo['valor'] = resumo['valor'].apply(lambda x: formata_br(x))
                st.dataframe(resumo, hide_index=True)
            else:
                st.info("O seu banco de dados na nuvem ainda está vazio.")
                
        except Exception as e:
             st.error(f"Erro no banco de dados: {e}")

    with aba_dashboard:
        st.header("📈 Projeções e Gráficos")
        
        try:
            with engine.connect() as conn:
                df_hist = pd.read_sql(text("SELECT * FROM historico WHERE usuario = :user"), conn, params={"user": usuario_atual})
            
            if not df_hist.empty:
                df_gastos = df_hist[df_hist['tipo'] == 'Saída'].copy()
                df_gastos['valor'] = df_gastos['valor'].abs() 
                
                st.subheader("Comparação de Gastos Mês a Mês")
                df_agrupado = df_gastos.groupby(['mes_ano', 'categoria'])['valor'].sum().reset_index()
                
                fig = px.bar(df_agrupado, x='mes_ano', y='valor', color='categoria', 
                             barmode='group', text_auto='.2f',
                             labels={'mes_ano': 'Mês / Ano', 'valor': 'Gasto (R$)', 'categoria': 'Categoria'},
                             color_discrete_map={
                                 'Contas (50%)': '#EF553B',
                                 'Lazer (20%)': '#00CC96',
                                 'Reserva (30%)': '#636EFA',
                                 'Outros': '#FFA15A'
                             })
                fig.update_layout(xaxis_type='category')
                st.plotly_chart(fig, use_container_width=True)

                st.divider()
                st.subheader("🔮 Provisionamento de Despesas Fixas")
                st.write("Abaixo está a projeção das suas **Contas (50%)**, calculada sempre com base no **último valor que você pagou**.")
                
                df_contas = df_gastos[df_gastos['categoria'] == 'Contas (50%)'].copy()
                
                if not df_contas.empty:
                    df_contas = df_contas.sort_values(by='id', ascending=False)
                    df_ultimas_contas = df_contas.drop_duplicates(subset=['descricao'], keep='first')
                    
                    df_exibicao_contas = df_ultimas_contas[['descricao', 'valor']].copy()
                    df_exibicao_contas.columns = ['Conta Registrada', 'Último Valor Pago (Projeção)']
                    
                    st.dataframe(
                        df_exibicao_contas.style.format({'Último Valor Pago (Projeção)': 'R$ {:.2f}'}), 
                        hide_index=True, 
                        use_container_width=True
                    )
                    
                    total_projetado = df_exibicao_contas['Último Valor Pago (Projeção)'].sum()
                    st.metric(label="Valor Total Provisionado para Contas", value=formata_br(total_projetado))
                else:
                    st.info("Ainda não há contas fixas registradas para gerar o provisionamento.")

            else:
                st.info("O seu banco de dados na nuvem está vazio.")
                
        except Exception as e:
            st.error(f"Erro no dashboard: {e}")

    with aba_ia:
        st.header("🤖 Seu Assistente Financeiro")
        st.write("Converse com a IA sobre Seus gastos. Ela já conhece o seu histórico!")
        
        if GROQ_API_KEY != "":
            if st.button("Analisar minhas finanças com IA", type="primary"):
                with st.spinner("Conectando à IA..."):
                    try:
                        with engine.connect() as conn:
                            df_hist = pd.read_sql(text("SELECT * FROM historico WHERE usuario = :user"), conn, params={"user": usuario_atual})
                        
                        if df_hist.empty:
                            st.info("Você precisa ter dados salvos no banco para a IA analisar.")
                        else:
                            df_saidas = df_hist[df_hist['tipo'] == 'Saída']
                            resumo = "Gastos por categoria:\n" + "\n".join([f"- {c}: R$ {v:.2f}" for c, v in df_saidas.groupby('categoria')['valor'].sum().abs().items()])
                            
                            # Configurando o cliente apontando para a Groq
                            client = openai.OpenAI(
                                api_key=GROQ_API_KEY,
                                base_url="https://api.groq.com/openai/v1"
                            )
                            
                            system_prompt = "Atue como um consultor financeiro especialista na regra 50/30/20. Responda obrigatoriamente em Português do Brasil (PT-BR)."
                            user_prompt = f"Aqui estão os dados financeiros do usuário: {resumo}. Analise o balanço e forneça 3 dicas práticas de onde melhorar."
                            
                            response = client.chat.completions.create(
                                model="llama-3.3-70b-versatile",
                                messages=[
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_prompt}
                                ]
                            )
                            
                            resposta_texto = response.choices[0].message.content
                            st.success("✅ Análise gerada com sucesso pela Groq (Gratuito)!")
                            st.write(resposta_texto)
                                
                    except Exception as e:
                        st.error(f"Erro ao comunicar com a Groq: {e}")
        else:
            st.warning("⚠️ Chave de API da Groq não configurada nos secrets.")

# ==========================================
# 3. O SISTEMA DE LOGIN E CADASTRO
# ==========================================
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False

if not st.session_state['autenticado']:
    st.markdown("<h1 style='text-align: center;'>🔒 Acesso ao Sistema</h1>", unsafe_allow_html=True)
    st.write("---")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        aba_login, aba_cadastro, aba_senha = st.tabs(["Entrar", "Criar Nova Conta", "Alterar Senha"])
        
        with aba_login:
            st.write("Insira suas credenciais para acessar o painel.")
            with st.form("form_login"):
                usuario_login = st.text_input("Usuário")
                senha_login = st.text_input("Senha", type="password")
                submit_login = st.form_submit_button("Entrar", use_container_width=True)
                
                if submit_login:
                    if usuario_login and senha_login:
                        with engine.connect() as conn:
                            usuario_encontrado = conn.execute(
                                text("SELECT * FROM usuarios WHERE usuario = :u AND senha = :s"), 
                                {"u": usuario_login, "s": senha_login}
                            ).fetchone()
                        
                        if usuario_encontrado:
                            st.session_state['autenticado'] = True
                            st.session_state['usuario'] = usuario_login
                            st.rerun()
                        else:
                            st.error("⚠️ Usuário ou senha incorretos.")
                    else:
                        st.warning("Preencha todos os campos.")
                    
        with aba_cadastro:
            st.write("Cadastre um novo usuário para acessar o sistema.")
            with st.form("form_cadastro"):
                novo_usuario = st.text_input("Novo Usuário")
                nova_senha = st.text_input("Nova Senha", type="password")
                confirma_senha = st.text_input("Confirme a Senha", type="password")
                submit_cadastro = st.form_submit_button("Cadastrar", use_container_width=True)
                
                if submit_cadastro:
                    if novo_usuario and nova_senha and confirma_senha:
                        if nova_senha == confirma_senha:
                            try:
                                with engine.begin() as conn:
                                    conn.execute(
                                        text("INSERT INTO usuarios (usuario, senha) VALUES (:u, :s)"), 
                                        {"u": novo_usuario, "s": nova_senha}
                                    )
                                st.success(f"✅ Conta criada! O usuário '{novo_usuario}' já pode fazer login na aba 'Entrar'.")
                            except IntegrityError:
                                st.error("⚠️ Este usuário já está cadastrado! Escolha outro nome ou vá para a aba 'Entrar'.")
                        else:
                            st.error("⚠️ As senhas não coincidem.")
                    else:
                        st.warning("Preencha todos os campos.")

        with aba_senha:
            st.write("Atualize a sua senha de acesso.")
            with st.form("form_senha"):
                alt_usuario = st.text_input("Usuário")
                alt_senha_atual = st.text_input("Senha Atual", type="password")
                alt_nova_senha = st.text_input("Nova Senha", type="password")
                submit_senha = st.form_submit_button("Atualizar Senha", use_container_width=True)
                
                if submit_senha:
                    if alt_usuario and alt_senha_atual and alt_nova_senha:
                        with engine.connect() as conn:
                            res = conn.execute(
                                text("SELECT * FROM usuarios WHERE usuario = :u AND senha = :s"), 
                                {"u": alt_usuario, "s": alt_senha_atual}
                            ).fetchone()
                            
                            if res:
                                conn.execute(
                                    text("UPDATE usuarios SET senha = :ns WHERE usuario = :u"), 
                                    {"ns": alt_nova_senha, "u": alt_usuario}
                                )
                                st.success("✅ Senha alterada com sucesso! Você já pode fazer login.")
                            else:
                                st.error("⚠️ Usuário ou senha atual incorretos.")
                    else:
                        st.warning("Preencha todos os campos.")
else:
    tela_principal()
