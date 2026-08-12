import streamlit as st
import pandas as pd
import pdfplumber
import re
import sqlite3
import plotly.express as px
import google.generativeai as genai

# === 🔑 SUA CHAVE SECRETA DA IA AQUI ===
CHAVE_API_GEMINI = "AQ.Ab8RN6L4f9vSR21Vt8S7k6g9jDLbXK6koPSgBw7CKfJwRreKkQ"
# =======================================

st.set_page_config(page_title="Controle Financeiro", layout="centered", page_icon="💸")


# ==========================================
# 1. FUNÇÕES BASE E BANCO DE DADOS
# ==========================================
def conectar_banco():
    conn = sqlite3.connect('meu_controle.db')

    # Cria a tabela de histórico já com a coluna 'usuario'
    conn.execute('''
        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT,
            mes_ano TEXT,
            descricao TEXT,
            categoria TEXT,
            valor REAL,
            tipo TEXT,
            usuario TEXT
        )
    ''')

    # ATUALIZAÇÃO AUTOMÁTICA (Migração para não perder seus testes anteriores)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(historico)")
    colunas = [col[1] for col in cursor.fetchall()]
    if 'usuario' not in colunas:
        conn.execute("ALTER TABLE historico ADD COLUMN usuario TEXT DEFAULT 'Christian'")
        conn.commit()

    conn.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            usuario TEXT PRIMARY KEY,
            senha TEXT
        )
    ''')

    cursor.execute("SELECT COUNT(*) FROM usuarios")
    if cursor.fetchone()[0] == 0:
        conn.execute("INSERT INTO usuarios (usuario, senha) VALUES ('Christian', '1234')")
        conn.commit()

    return conn


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
    if any(p in desc_min for p in ['remuneracao/salario', 'somos educacao', 'pix transf christi']):
        return 'Ignorar'
    elif any(p in desc_min for p in ['claro', 'moura', 'oficina', 'mecânica', 'energia', 'água', 'iptu', 'ipva']):
        return 'Contas (50%)'
    elif any(p in desc_min for p in ['investimento', 'cdb', 'tesouro', 'cofrinho', 'guardado']):
        return 'Reserva (30%)'
    elif any(p in desc_min for p in
             ['epic game', 'steam', 'playstation', 'nintendo', 'outback', 'habib', 'boigalê', 'di paolo', 'ifood',
              'ingresso']):
        return 'Lazer (20%)'
    return 'Outros'


def formata_br(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ==========================================
# 2. O APLICATIVO PRINCIPAL (Protegido e Isolado)
# ==========================================
def tela_principal():
    # Guarda o nome de quem está logado numa variável curta para facilitar
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

    # === ABA 1: IMPORTAÇÃO E CLASSIFICAÇÃO ===
    with aba_importacao:
        st.write("Faça o upload do extrato para classificar e salvar no sistema.")
        arquivo_upload = st.file_uploader("Selecione o arquivo do extrato", type=["pdf", "csv", "xlsx"])

        if arquivo_upload is not None:
            try:
                if arquivo_upload.name.endswith('.pdf'):
                    df = extrair_dados_pdf(arquivo_upload)
                elif arquivo_upload.name.endswith('.csv'):
                    df = pd.read_csv(arquivo_upload, sep=',')
                else:
                    df = pd.read_excel(arquivo_upload)

                if 'valor' in df.columns:
                    df['valor_calculo'] = df['valor'].astype(str).str.replace('R$', '', regex=False).str.replace(' ',
                                                                                                                 '',
                                                                                                                 regex=False).str.replace(
                        '.', '', regex=False).str.replace(',', '.', regex=False)
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

                    filtro_salario = df_mes['origem / destino'].str.lower().str.contains(
                        'remuneracao/salario|salário|salario|pagamento', na=False)
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
                            column_config={"Categoria": st.column_config.SelectboxColumn(
                                options=['Contas (50%)', 'Reserva (30%)', 'Lazer (20%)', 'Ignorar', 'Outros'],
                                required=True),
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
                            c1.metric("Contas (50%)", formata_br(real_essencial),
                                      f"{(salario_identificado * 0.50) - real_essencial:,.2f} livre")
                            c2.metric("Reserva (30%)", formata_br(real_reserva),
                                      f"{(salario_identificado * 0.30) - real_reserva:,.2f} livre")
                            c3.metric("Lazer (20%)", formata_br(real_lazer),
                                      f"{(salario_identificado * 0.20) - real_lazer:,.2f} livre")

                        st.divider()
                        st.subheader("💾 Salvar no Sistema")

                        if st.button("Gravar no Banco de Dados", type="primary"):
                            df_para_banco = df_editado[df_editado['Categoria'] != 'Ignorar'].copy()
                            df_para_banco['tipo'] = df_para_banco['valor_calculo'].apply(
                                lambda x: 'Entrada' if x > 0 else 'Saída')

                            # Carimba os dados com o nome do usuário logado
                            df_para_banco['usuario'] = usuario_atual

                            dados_sql = df_para_banco[
                                ['data', 'mes_ano', 'origem / destino', 'Categoria', 'valor_calculo', 'tipo',
                                 'usuario']]
                            dados_sql.columns = ['data', 'mes_ano', 'descricao', 'categoria', 'valor', 'tipo',
                                                 'usuario']

                            conn = conectar_banco()
                            # Apaga as duplicatas apenas DESTE usuário neste mês
                            conn.execute("DELETE FROM historico WHERE mes_ano = ? AND usuario = ?",
                                         (mes_selecionado, usuario_atual))
                            dados_sql.to_sql('historico', conn, if_exists='append', index=False)
                            conn.commit()
                            conn.close()

                            st.success(
                                f"Sucesso! Lançamentos de {mes_selecionado} foram gravados na sua conta. Verifique a aba 'Meu Banco de Dados'.")
                    else:
                        st.warning("Salário não encontrado neste mês.")
            except Exception as e:
                st.error(f"Erro: {e}")

    # === ABA 2: VISUALIZAÇÃO E EDIÇÃO DO BANCO ===
    with aba_historico:
        st.header("🗄️ Histórico Completo e Edição")
        st.write(
            "Dê dois cliques na célula para editar, ou selecione uma linha inteira e aperte `Delete` para apagá-la.")

        try:
            conn = conectar_banco()
            # Puxa apenas os dados do usuário logado
            df_historico = pd.read_sql("SELECT * FROM historico WHERE usuario = ? ORDER BY id DESC", conn,
                                       params=(usuario_atual,))
            conn.close()

            if not df_historico.empty:
                df_editado_db = st.data_editor(
                    df_historico,
                    num_rows="dynamic",
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "id": st.column_config.NumberColumn(disabled=True),
                        "categoria": st.column_config.SelectboxColumn(
                            options=['Contas (50%)', 'Reserva (30%)', 'Lazer (20%)', 'Outros']),
                        "usuario": None  # Esconde a coluna do usuário para não poder ser editada na tela
                    }
                )

                if st.button("Salvar Alterações no Banco"):
                    conn = conectar_banco()
                    # Apaga apenas os dados desse usuário para substituir pelos editados
                    conn.execute("DELETE FROM historico WHERE usuario = ?", (usuario_atual,))
                    df_editado_db.to_sql('historico', conn, if_exists='append', index=False)
                    conn.commit()
                    conn.close()
                    st.success("Alterações gravadas permanentemente! Os gráficos já estão atualizados.")

                st.divider()
                st.subheader("Resumo por Categoria (Todo o período)")
                resumo = df_historico[df_historico['tipo'] == 'Saída'].groupby('categoria')[
                    'valor'].sum().abs().reset_index()
                resumo['valor'] = resumo['valor'].apply(lambda x: formata_br(x))
                st.dataframe(resumo, hide_index=True)
            else:
                st.info("O seu banco de dados pessoal ainda está vazio.")

        except Exception as e:
            st.error(f"Erro no banco de dados: {e}")

    # === ABA 3: DASHBOARD E PROVISÕES ===
    with aba_dashboard:
        st.header("📈 Projeções e Gráficos")

        try:
            conn = conectar_banco()
            # Puxa apenas os dados do usuário logado
            df_hist = pd.read_sql("SELECT * FROM historico WHERE usuario = ?", conn, params=(usuario_atual,))
            conn.close()

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
                st.write(
                    "Abaixo está a projeção das suas **Contas (50%)**, calculada sempre com base no **último valor que você pagou**.")

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
                st.info("O seu banco de dados pessoal está vazio.")

        except Exception as e:
            st.error(f"Erro no dashboard: {e}")

    # === ABA 4: O CONSELHEIRO IA ===
    with aba_ia:
        st.header("🤖 Seu Assistente Financeiro")
        st.write("Converse com a IA sobre seus gastos. Ela já conhece o seu histórico!")

        try:
            conn = conectar_banco()
            # Puxa apenas os dados do usuário logado
            df_hist = pd.read_sql("SELECT * FROM historico WHERE usuario = ?", conn, params=(usuario_atual,))
            conn.close()

            if CHAVE_API_GEMINI != "" and CHAVE_API_GEMINI != "COLE_A_SUA_CHAVE_AQUI_DENTRO_DAS_ASPAS":
                if not df_hist.empty:
                    genai.configure(api_key=CHAVE_API_GEMINI)

                    if st.button("Analisar minhas finanças", type="primary"):
                        with st.spinner("A IA está analisando seus dados, procurando o melhor modelo..."):

                            df_entradas = df_hist[df_hist['tipo'] == 'Entrada']
                            df_saidas = df_hist[df_hist['tipo'] == 'Saída']

                            renda_total = df_entradas['valor'].sum()

                            resumo_texto = f"Renda Total Registrada: R$ {renda_total:.2f}\n\nGastos por Categoria:\n"

                            gastos_por_categoria = df_saidas.groupby('categoria')['valor'].sum().abs()
                            for cat, val in gastos_por_categoria.items():
                                resumo_texto += f"- {cat}: R$ {val:.2f}\n"

                            prompt = f"""
                            Atue como um consultor financeiro especialista na regra 50/30/20.

                            REGRA OBRIGATÓRIA: Responda SEMPRE e EXCLUSIVAMENTE em Português do Brasil (PT-BR). 
                            Não inclua pensamentos, etapas ou cabeçalhos em inglês na sua resposta.

                            Aqui estão os dados financeiros do usuário (acumulado do período):
                            {resumo_texto}

                            Analise o balanço entre a Renda Total e os Gastos. 
                            Com base na regra 50/30/20, forneça 3 dicas práticas de onde o usuário pode melhorar.
                            """

                            modelo_funcionando = None
                            resposta_ia = ""

                            modelos_texto = [m.name for m in genai.list_models() if
                                             'generateContent' in m.supported_generation_methods]

                            for nome_modelo in modelos_texto:
                                try:
                                    modelo = genai.GenerativeModel(nome_modelo)
                                    resposta = modelo.generate_content(prompt)
                                    resposta_ia = resposta.text
                                    modelo_funcionando = nome_modelo
                                    break
                                except Exception:
                                    continue

                            if modelo_funcionando:
                                st.caption(f"✅ Análise gerada com sucesso usando o modelo: **{modelo_funcionando}**")
                                st.write(resposta_ia)
                            else:
                                st.error(
                                    "Nenhum modelo compatível foi encontrado. Verifique as restrições da sua conta no Google AI Studio.")
                else:
                    st.info("Você precisa ter dados salvos no banco para a IA analisar.")
            else:
                st.warning(
                    "⚠️ Você precisa colar a sua Chave de API na linha 10 do código fonte para liberar esta aba!")

        except Exception as e:
            st.error(f"Erro na IA: {e}")


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

        # --- LÓGICA DE LOGIN ---
        with aba_login:
            st.write("Insira suas credenciais para acessar o painel.")
            usuario_login = st.text_input("Usuário", key="log_user")
            senha_login = st.text_input("Senha", type="password", key="log_pass")

            if st.button("Entrar", type="primary", use_container_width=True):
                if usuario_login and senha_login:
                    conn = conectar_banco()
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM usuarios WHERE usuario = ? AND senha = ?",
                                   (usuario_login, senha_login))
                    usuario_encontrado = cursor.fetchone()
                    conn.close()

                    if usuario_encontrado:
                        st.session_state['autenticado'] = True
                        st.session_state['usuario'] = usuario_login
                        st.rerun()
                    else:
                        st.error("⚠️ Usuário ou senha incorretos.")
                else:
                    st.warning("Preencha todos os campos.")

        # --- LÓGICA DE CADASTRO ---
        with aba_cadastro:
            st.write("Cadastre um novo usuário para acessar o sistema.")
            novo_usuario = st.text_input("Novo Usuário", key="cad_user")
            nova_senha = st.text_input("Nova Senha", type="password", key="cad_pass")
            confirma_senha = st.text_input("Confirme a Senha", type="password", key="cad_pass_conf")

            if st.button("Cadastrar", use_container_width=True):
                if novo_usuario and nova_senha and confirma_senha:
                    if nova_senha == confirma_senha:
                        conn = conectar_banco()
                        try:
                            conn.execute("INSERT INTO usuarios (usuario, senha) VALUES (?, ?)",
                                         (novo_usuario, nova_senha))
                            conn.commit()
                            st.success(
                                f"✅ Conta criada! O usuário '{novo_usuario}' já pode fazer login na aba 'Entrar'.")
                        except sqlite3.IntegrityError:
                            st.error(
                                "⚠️ Este usuário já está cadastrado! Escolha outro nome ou vá para a aba 'Entrar'.")
                        finally:
                            conn.close()
                    else:
                        st.error("⚠️ As senhas não coincidem.")
                else:
                    st.warning("Preencha todos os campos.")

        # --- LÓGICA DE ALTERAR SENHA ---
        with aba_senha:
            st.write("Atualize a sua senha de acesso.")
            alt_usuario = st.text_input("Usuário", key="alt_user")
            alt_senha_atual = st.text_input("Senha Atual", type="password", key="alt_pass_atual")
            alt_nova_senha = st.text_input("Nova Senha", type="password", key="alt_pass_nova")

            if st.button("Atualizar Senha", use_container_width=True):
                if alt_usuario and alt_senha_atual and alt_nova_senha:
                    conn = conectar_banco()
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM usuarios WHERE usuario = ? AND senha = ?",
                                   (alt_usuario, alt_senha_atual))
                    if cursor.fetchone():
                        conn.execute("UPDATE usuarios SET senha = ? WHERE usuario = ?", (alt_nova_senha, alt_usuario))
                        conn.commit()
                        st.success("✅ Senha alterada com sucesso! Você já pode fazer login.")
                    else:
                        st.error("⚠️ Usuário ou senha atual incorretos.")
                    conn.close()
                else:
                    st.warning("Preencha todos os campos.")
else:
    tela_principal()