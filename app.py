import streamlit as st
import pandas as pd
import datetime
from streamlit_gsheets import GSheetsConnection
import plotly.express as px

# --- 1. UI・システム設定 ---
st.set_page_config(
    page_title="せつやくん - 予測型資産管理 完全版",
    page_icon="https://raw.githubusercontent.com/kiyomunga/setuyakun_dev/main/icon.png",
    layout="wide"
)

conn = st.connection("gsheets", type=GSheetsConnection)

def fetch_data(worksheet, cols):
    df = conn.read(worksheet=worksheet, usecols=list(range(cols)))
    return df.dropna(how="all")

def get_sources(): return fetch_data("sources", 2)
def update_sources(df): conn.update(worksheet="sources", data=df)

def get_transactions(): return fetch_data("transactions", 7)
def update_transactions(df): conn.update(worksheet="transactions", data=df)

def get_events():
    df = fetch_data("events", 8)
    if not df.empty:
        df['is_included'] = df['is_included'].replace({'TRUE': True, 'FALSE': False, 'True': True, 'False': False}).fillna(False).astype(bool)
        df['is_completed'] = df['is_completed'].replace({'TRUE': True, 'FALSE': False, 'True': True, 'False': False}).fillna(False).astype(bool)
        df['対象年'] = pd.to_numeric(df['対象年'], errors='coerce').fillna(datetime.datetime.now().year)
    return df
def update_events(df): conn.update(worksheet="events", data=df)

def get_event_details(): return fetch_data("event_details", 3)
def update_event_details(df): conn.update(worksheet="event_details", data=df)

def get_config(): return fetch_data("config", 4)
def update_config(df): conn.update(worksheet="config", data=df)

def get_fixed_costs(): return fetch_data("fixed_costs", 3)
def update_fixed_costs(df): conn.update(worksheet="fixed_costs", data=df)

def get_income_details(): return fetch_data("income_details", 3)
def update_income_details(df): conn.update(worksheet="income_details", data=df)

# --- 2. 数式パーサー（3000*5等の計算処理） ---
def parse_math(expr):
    if not expr: return 0
    try:
        expr_clean = str(expr).replace('×', '*').replace('÷', '/').replace('＋', '+').replace('－', '-')
        return int(eval(expr_clean, {"__builtins__": {}}))
    except:
        return None

# --- 3. 演算エンジン（予算ベース余剰プールと予実マージ） ---
def calculate_system_state(target_year):
    state = {}
    
    df_src = get_sources()
    df_tx = get_transactions()
    df_tx['金額'] = pd.to_numeric(df_tx['金額'], errors='coerce').fillna(0)
    df_tx['イベントID'] = df_tx['イベントID'].fillna('')
    df_tx['年'] = pd.to_datetime(df_tx['日付'], errors='coerce').dt.year
    
    # 総資産計算
    balances = {str(row['財源名']): int(row['初期残高']) for _, row in df_src.iterrows()}
    if not df_tx.empty:
        for _, row in df_tx.iterrows():
            src, dest, amt = str(row['出金元']), str(row['入金先']), int(row['金額'])
            if src in balances: balances[src] -= amt
            if dest in balances: balances[dest] += amt
    state['total_assets'] = sum(balances.values()) if balances else 0
    state['balances'] = balances
    
    # 予算・収入・固定費の設定値フェッチ
    df_conf = get_config()
    df_conf['対象年'] = pd.to_numeric(df_conf['対象年'], errors='coerce')
    conf_target = df_conf[df_conf['対象年'] == target_year]
    base_saving = int(conf_target.iloc[0]['前年末貯蓄']) if not conf_target.empty else 0
    target_saving = int(conf_target.iloc[0]['目標貯蓄']) if not conf_target.empty else 0
    
    df_inc_det = get_income_details()
    total_income = 0
    if not df_inc_det.empty:
        df_inc_det['対象年'] = pd.to_numeric(df_inc_det['対象年'], errors='coerce')
        df_inc_det['金額'] = pd.to_numeric(df_inc_det['金額'], errors='coerce').fillna(0)
        total_income = int(df_inc_det[df_inc_det['対象年'] == target_year]['金額'].sum())
        
    df_fc = get_fixed_costs()
    fc_total = 0
    if not df_fc.empty:
        df_fc['対象年'] = pd.to_numeric(df_fc['対象年'], errors='coerce')
        df_fc['予算額'] = pd.to_numeric(df_fc['予算額'], errors='coerce').fillna(0)
        fc_total = int(df_fc[df_fc['対象年'] == target_year]['予算額'].sum())

    # イベント予算の計算
    df_ev = get_events()
    df_ev_details = get_event_details()
    if not df_ev.empty:
        if not df_ev_details.empty:
            df_ev_details['予算額'] = pd.to_numeric(df_ev_details['予算額'], errors='coerce').fillna(0)
            budget_map = df_ev_details.groupby('イベントID')['予算額'].sum().to_dict()
            df_ev['総予算'] = df_ev['イベントID'].map(budget_map).fillna(0)
        else:
            df_ev['総予算'] = pd.to_numeric(df_ev['総予算'], errors='coerce').fillna(0)
            
    df_ev_y = df_ev[df_ev['対象年'] == target_year] if not df_ev.empty else pd.DataFrame()
    ev_total = df_ev_y['総予算'].sum() if not df_ev_y.empty else 0

    # トランザクション実績の集計
    df_exp_year = df_tx[(df_tx['入金先'] == '外部') & (df_tx['年'] == target_year)]
    df_inc_year = df_tx[(df_tx['出金元'] == '外部') & (df_tx['年'] == target_year)]
    event_actuals = df_exp_year.groupby('イベントID')['金額'].sum().to_dict()
    
    # 差分マージ（完了イベントの予実差分を抽出）
    completed_delta = 0
    if not df_ev_y.empty:
        df_ev_y['実績額'] = df_ev_y['イベントID'].map(event_actuals).fillna(0)
        completed_events = df_ev_y[df_ev_y['is_completed'] == True]
        for _, ev in completed_events.iterrows():
            completed_delta += (ev['総予算'] - ev['実績額'])
            
    extra_income = df_inc_year[df_inc_year['カテゴリ'] == '臨時収入']['金額'].sum()

    # 予算ベースの余剰プール計算
    budget_surplus = (base_saving + total_income) - target_saving - fc_total - ev_total
    current_surplus = budget_surplus + completed_delta + extra_income
    state['surplus_pool'] = current_surplus
    
    # 今月の利用可能額計算
    now = datetime.datetime.now()
    if target_year == now.year:
        df_exp_base_ytd = df_exp_year[df_exp_year['イベントID'] == '']
        spent_ytd = df_exp_base_ytd['金額'].sum()
        
        current_month_str = now.strftime('%Y-%m')
        spent_this_month = df_exp_base_ytd[df_exp_base_ytd['日付'].str.startswith(current_month_str)]['金額'].sum()
        spent_before_this_month = spent_ytd - spent_this_month
        
        budget_at_start_of_month = current_surplus - spent_before_this_month
        remaining_months = 12 - now.month + 1
        
        monthly_limit = budget_at_start_of_month / remaining_months if remaining_months > 0 else 0
        this_month_remaining = monthly_limit - spent_this_month
        
        state['monthly_limit'] = monthly_limit
        state['this_month_spent'] = spent_this_month
        state['this_month_remaining'] = this_month_remaining
    else:
        state['monthly_limit'] = current_surplus / 12
        state['this_month_spent'] = 0
        state['this_month_remaining'] = 0
        
    return state, df_tx, df_ev, df_conf, df_ev_details, df_fc, df_inc_det

# --- 4. フロントエンド・レンダリング ---
st.title('資産管理システム Ver.7.1 (完全統合作業環境)')

current_year_default = datetime.datetime.now().year
target_year_ui = st.sidebar.number_input("📅 基準年（全体対象）", min_value=2020, max_value=2050, value=current_year_default, step=1)

state, df_tx, df_ev, df_conf, df_ev_details, df_fc, df_inc_det = calculate_system_state(target_year_ui)

tab1, tab2, tab3, tab4, tab5 = st.tabs(['📊 ダッシュボード', '📝 記録＆履歴', '✍️ 予算案入力', '👁️ 予算案確認', '🎪 イベント状態制御'])

# ==========================================
# タブ1：ダッシュボード
# ==========================================
with tab1:
    st.header(f"🎯 KGIマイルストーン & 実績監視 ({target_year_ui}年)")
    col1, col2, col3 = st.columns(3)
    col1.metric("現在の総資産額", f"¥{state['total_assets']:,}")
    col2.metric(f"余剰プール（予算+差分還元）", f"¥{int(state['surplus_pool']):,}", "固定・イベント控除後 ＋ 完了イベント浮き分")
    
    if target_year_ui == current_year_default:
        remaining = int(state['this_month_remaining'])
        delta_color = "normal" if remaining >= 0 else "inverse"
        col3.metric(f"{datetime.datetime.now().month}月の自由利用残枠", f"¥{remaining:,}", f"今月消費: ¥{int(state['this_month_spent']):,}", delta_color=delta_color)
    else:
        col3.metric("月平均利用可能枠", f"¥{int(state['monthly_limit']):,}")

    st.write("---")
    st.subheader(f'月別・目的別 支出分析 ({target_year_ui}年)')
    if not df_tx.empty:
        df_exp = df_tx[(df_tx['入金先'] == '外部') & (df_tx['年'] == target_year_ui)].copy()
        if not df_exp.empty:
            df_exp['年月'] = pd.to_datetime(df_exp['日付']).dt.strftime('%Y-%m')
            months = sorted(df_exp['年月'].unique(), reverse=True)
            selected_month = st.selectbox('分析対象月', months)
            df_month = df_exp[df_exp['年月'] == selected_month]
            if not df_month.empty:
                df_grouped = df_month.groupby('カテゴリ')['金額'].sum().reset_index()
                fig = px.pie(df_grouped, values='金額', names='カテゴリ', hole=0.4, title=f'{selected_month} リソース配分比率', color_discrete_sequence=px.colors.qualitative.Pastel)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info('選択した月のデータがありません。')
        else:
            st.info('支出データがありません。')

# ==========================================
# タブ2：トランザクション記録＆履歴 (UI色彩最適化)
# ==========================================
with tab2:
    st.header('📝 お金の出入りを記録')
    source_options = ['外部'] + list(state['balances'].keys()) if state['balances'] else ['外部']
    category_options = ['食費', '学業', '移動費', 'スポーツ', '趣味・娯楽', '日用品', '交際費', '資金移動', '臨時収入', 'その他']
    event_options = [''] + df_ev[df_ev['対象年'] == target_year_ui]['イベントID'].tolist() if not df_ev.empty else ['']
    
    with st.form(key='tx_form', clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            tx_date = st.date_input('日付', datetime.date.today())
            tx_src = st.selectbox('出金先', source_options, index=0)
            tx_dest = st.selectbox('入金先', source_options, index=0 if len(source_options) == 1 else 1)
            tx_amt = st.text_input('金額（数式可 例: 3000*5）', value="0")
        with col2:
            tx_cat = st.selectbox('カテゴリ', category_options)
            tx_event = st.selectbox(f'紐付けイベント ({target_year_ui}年)', event_options)
            tx_memo = st.text_input('メモ')
        if st.form_submit_button('トランザクションを実行'):
            parsed_amt = parse_math(tx_amt)
            if parsed_amt is None or parsed_amt <= 0: st.error('金額の入力または数式が不正です。')
            elif tx_src == '外部' and tx_dest == '外部': st.error('無効な取引です。')
            elif tx_src == tx_dest: st.error('同じ財源が選択されています。')
            else:
                new_row = [tx_date.strftime('%Y-%m-%d'), tx_src, tx_dest, parsed_amt, tx_memo, tx_cat, tx_event]
                if df_tx.empty: df_tx = pd.DataFrame(columns=['日付', '出金元', '入金先', '金額', 'メモ', 'カテゴリ', 'イベントID'])
                df_tx.loc[len(df_tx)] = new_row
                update_transactions(df_tx)
                st.cache_data.clear()
                st.rerun()

    st.write('---')
    st.subheader('📜 取引履歴一覧（直近50件）')
    if not df_tx.empty:
        df_display = df_tx.copy()
        
        # UI色彩判定ロジック
        def format_impact(row):
            try:
                amt = int(row['金額'])
            except:
                amt = 0
            if row['出金元'] == '外部' and row['入金先'] != '外部':
                return f"+ ¥{amt:,}"
            elif row['入金先'] == '外部' and row['出金元'] != '外部':
                return f"- ¥{amt:,}"
            else:
                return f"± ¥{amt:,}"
                
        df_display['金額'] = df_display.apply(format_impact, axis=1)
        
        def color_impact(val):
            if isinstance(val, str):
                if val.startswith('+'):
                    return 'color: #00BFFF; font-weight: bold;'
                elif val.startswith('-'):
                    return 'color: #FF4B4B; font-weight: bold;'
            return ''
            
        df_reversed = df_display.iloc[::-1].head(50)
        try:
            df_styled = df_reversed.style.map(color_impact, subset=['金額'])
        except AttributeError:
            df_styled = df_reversed.style.applymap(color_impact, subset=['金額'])
            
        st.dataframe(df_styled, use_container_width=True)

# ==========================================
# タブ3：予算案入力 
# ==========================================
with tab3:
    st.header("✍️ 予算案入力タブ")
    
    st.subheader("⚙️ 全体予算ベース設定")
    with st.form(key='config_form'):
        col_y, col_b, col_t = st.columns(3)
        with col_y: input_year = st.number_input("対象年", value=target_year_ui, step=1)
        with col_b: in_base = st.text_input("前年末貯蓄（数式可）", value="0")
        with col_t: in_target = st.text_input("当年末目標貯蓄（数式可）", value="0")
        if st.form_submit_button("ベース設定を保存"):
            p_base, p_target = parse_math(in_base), parse_math(in_target)
            if p_base is not None and p_target is not None:
                if df_conf.empty: df_conf = pd.DataFrame(columns=['対象年', '前年末貯蓄', '予測収入', '目標貯蓄'])
                df_conf = df_conf[df_conf['対象年'] != input_year] 
                df_conf.loc[len(df_conf)] = [input_year, p_base, 0, p_target] 
                update_config(df_conf)
                st.cache_data.clear()
                st.rerun()
            else:
                st.error("入力された数式が不正です。")

    st.write("---")
    st.subheader("💰 当年予測収入内訳")
    with st.form(key='inc_form', clear_on_submit=True):
        inc_y, inc_n, inc_a = st.columns(3)
        with inc_y: inc_year = st.number_input("対象年", value=target_year_ui, step=1, key='incy')
        with inc_n: inc_name = st.text_input("項目名（例：東進バイト）")
        with inc_a: inc_amt_str = st.text_input("金額（数式可 例: 80000*12）", value="0")
        if st.form_submit_button("収入内訳を登録"):
            p_inc = parse_math(inc_amt_str)
            if inc_name and p_inc is not None:
                if df_inc_det.empty: df_inc_det = pd.DataFrame(columns=['対象年', '項目名', '金額'])
                df_inc_det.loc[len(df_inc_det)] = [inc_year, inc_name, p_inc]
                update_income_details(df_inc_det)
                st.cache_data.clear()
                st.rerun()

    st.write("---")
    st.subheader("固定費用")
    with st.form(key='fc_form', clear_on_submit=True):
        fc_y, fc_n, fc_a = st.columns(3)
        with fc_y: fc_year = st.number_input("対象年", value=target_year_ui, step=1, key='fcy')
        with fc_n: fc_name = st.text_input("項目名（例：Gemini）")
        with fc_a: fc_amt_str = st.text_input("予算額（数式可 例: 1500*12）", value="0")
        if st.form_submit_button("固定費用を登録"):
            p_fc = parse_math(fc_amt_str)
            if fc_name and p_fc is not None:
                if df_fc.empty: df_fc = pd.DataFrame(columns=['対象年', '項目名', '予算額'])
                df_fc.loc[len(df_fc)] = [fc_year, fc_name, p_fc]
                update_fixed_costs(df_fc)
                st.cache_data.clear()
                st.rerun()

    st.write("---")
    st.subheader("イベント登録")
    with st.form(key='ev_reg_form', clear_on_submit=True):
        ev_y, ev_i, ev_n = st.columns(3)
        with ev_y: ev_year = st.number_input("対象年", value=target_year_ui, step=1, key='evy')
        with ev_i: ev_id = st.text_input("イベントID（例：2026-01）")
        with ev_n: ev_name = st.text_input("イベント名（例：夏合宿）")
        if st.form_submit_button("イベントを作成"):
            if ev_id and ev_name:
                if df_ev.empty: df_ev = pd.DataFrame(columns=['イベントID', 'イベント名', '総予算', '実績額', 'is_included', 'is_completed', '作成日', '対象年'])
                df_ev.loc[len(df_ev)] = [ev_id, ev_name, 0, 0, True, False, datetime.date.today().strftime('%Y-%m-%d'), ev_year]
                update_events(df_ev)
                st.cache_data.clear()
                st.rerun()

    st.write("---")
    st.subheader("イベント詳細内訳")
    if not df_ev.empty:
        df_ev_target_input = df_ev[df_ev['対象年'] == target_year_ui]
        ev_dict = dict(zip(df_ev_target_input['イベント名'], df_ev_target_input['イベントID'])) if not df_ev_target_input.empty else {}
        if ev_dict:
            with st.form(key='ev_det_form', clear_on_submit=True):
                sel_ev_name = st.selectbox(f"イベントを選択 ({target_year_ui}年)", list(ev_dict.keys()))
                det_name = st.text_input("項目名（例：宿泊費）")
                det_amt_str = st.text_input("予算額（数式可 例: 3000*5）", value="0")
                if st.form_submit_button("内訳を追加"):
                    p_det = parse_math(det_amt_str)
                    if det_name and p_det is not None:
                        if df_ev_details.empty: df_ev_details = pd.DataFrame(columns=['イベントID', '項目名', '予算額'])
                        df_ev_details.loc[len(df_ev_details)] = [ev_dict[sel_ev_name], det_name, p_det]
                        update_event_details(df_ev_details)
                        st.cache_data.clear()
                        st.rerun()
        else:
            st.info(f"{target_year_ui}年のイベントがありません。先にイベントを登録してください。")

    st.write("---")
    st.subheader("🗑️ 登録データの削除")
    del_c1, del_c2, del_c3 = st.columns(3)
    
    with del_c1:
        df_fc_target = df_fc[df_fc['対象年'] == target_year_ui] if not df_fc.empty else pd.DataFrame()
        fc_list = df_fc_target['項目名'].tolist() if not df_fc_target.empty else []
        with st.form(key='del_fc_form'):
            del_fc_name = st.selectbox("削除する固定費用", fc_list if fc_list else ["データなし"])
            if st.form_submit_button("削除"):
                if fc_list and del_fc_name != "データなし":
                    df_fc = df_fc[~((df_fc['対象年'] == target_year_ui) & (df_fc['項目名'] == del_fc_name))]
                    update_fixed_costs(df_fc)
                    st.cache_data.clear()
                    st.rerun()

        df_inc_target = df_inc_det[df_inc_det['対象年'] == target_year_ui] if not df_inc_det.empty else pd.DataFrame()
        inc_list = df_inc_target['項目名'].tolist() if not df_inc_target.empty else []
        with st.form(key='del_inc_form'):
            del_inc_name = st.selectbox("削除する収入内訳", inc_list if inc_list else ["データなし"])
            if st.form_submit_button("削除"):
                if inc_list and del_inc_name != "データなし":
                    df_inc_det = df_inc_det[~((df_inc_det['対象年'] == target_year_ui) & (df_inc_det['項目名'] == del_inc_name))]
                    update_income_details(df_inc_det)
                    st.cache_data.clear()
                    st.rerun()

    with del_c2:
        df_ev_target = df_ev[df_ev['対象年'] == target_year_ui] if not df_ev.empty else pd.DataFrame()
        ev_dict_del = dict(zip(df_ev_target['イベント名'], df_ev_target['イベントID'])) if not df_ev_target.empty else {}
        with st.form(key='del_ev_form'):
            del_ev_name = st.selectbox("削除するイベント本体", list(ev_dict_del.keys()) if ev_dict_del else ["データなし"])
            if st.form_submit_button("削除 (内訳も連動)"):
                if ev_dict_del and del_ev_name != "データなし":
                    target_id = ev_dict_del[del_ev_name]
                    df_ev = df_ev[df_ev['イベントID'] != target_id]
                    update_events(df_ev)
                    if not df_ev_details.empty:
                        df_ev_details = df_ev_details[df_ev_details['イベントID'] != target_id]
                        update_event_details(df_ev_details)
                    st.cache_data.clear()
                    st.rerun()

    with del_c3:
        if ev_dict_del:
            sel_ev_for_det = st.selectbox("対象イベント", list(ev_dict_del.keys()), key='del_ev_sel')
            target_id_det = ev_dict_del[sel_ev_for_det]
            det_list = df_ev_details[df_ev_details['イベントID'] == target_id_det]['項目名'].tolist() if not df_ev_details.empty else []
            with st.form(key='del_det_form'):
                del_det_name = st.selectbox("削除するイベント内訳", det_list if det_list else ["データなし"])
                if st.form_submit_button("削除"):
                    if det_list and del_det_name != "データなし":
                        df_ev_details = df_ev_details[~((df_ev_details['イベントID'] == target_id_det) & (df_ev_details['項目名'] == del_det_name))]
                        update_event_details(df_ev_details)
                        st.cache_data.clear()
                        st.rerun()

# ==========================================
# タブ4：予算案確認
# ==========================================
with tab4:
    st.header("👁️ 予算案確認タブ")
    target_year_budget = st.selectbox("確認対象年", range(2025, 2035), index=target_year_ui-2025)
    
    base, target = 0, 0
    if not df_conf.empty:
        conf_y = df_conf[df_conf['対象年'] == target_year_budget]
        if not conf_y.empty:
            base, target = int(conf_y.iloc[0]['前年末貯蓄']), int(conf_y.iloc[0]['目標貯蓄'])
            
    income_total = 0
    df_inc_y = pd.DataFrame()
    if not df_inc_det.empty:
        df_inc_det['対象年'] = pd.to_numeric(df_inc_det['対象年'], errors='coerce')
        df_inc_y = df_inc_det[df_inc_det['対象年'] == target_year_budget]
        income_total = int(df_inc_y['金額'].sum())
            
    available_total = (base + income_total) - target
    
    st.subheader("＜予算案＞")
    st.write(f"前年末貯蓄: {base:,}円 ... ①")
    st.markdown(f"**当年予測収入: {income_total:,}円 ... ②**")
    if not df_inc_y.empty:
        with st.expander("収入内訳"):
            for _, row in df_inc_y.iterrows():
                st.write(f"・{row['項目名']}: {int(row['金額']):,}円")
                
    st.write(f"当年末目標貯蓄: {target:,}円 ... ③")
    st.markdown(f"**当年利用可能金額 (総額) : ① + ② - ③ = {available_total:,}円**")
    
    st.write("---")
    st.subheader("＜出費＞")
    fc_total = 0
    if not df_fc.empty:
        df_fc_y = df_fc[df_fc['対象年'] == target_year_budget]
        fc_total = int(df_fc_y['予算額'].sum())
        
    st.markdown(f"**固定費用: {fc_total:,}円**")
    if not df_fc.empty and not df_fc_y.empty:
        with st.expander("内訳"):
            for _, row in df_fc_y.iterrows():
                st.write(f"・{row['項目名']}: {int(row['予算額']):,}円")
                
    st.write("") 
    ev_total_all = 0
    if not df_ev.empty:
        df_ev_y = df_ev[df_ev['対象年'] == target_year_budget]
        ev_display_data = []
        for _, ev_row in df_ev_y.iterrows():
            eid, ename = ev_row['イベントID'], ev_row['イベント名']
            det_df = df_ev_details[df_ev_details['イベントID'] == eid] if not df_ev_details.empty else pd.DataFrame()
            ev_sum = int(pd.to_numeric(det_df['予算額'], errors='coerce').fillna(0).sum()) if not det_df.empty else 0
            ev_total_all += ev_sum
            ev_display_data.append((ename, ev_sum, det_df))
            
        st.markdown(f"**イベント: {ev_total_all:,}円**")
        for ename, ev_sum, det_df in ev_display_data:
            with st.expander(f"{ename}: {ev_sum:,}円"):
                with st.expander("内訳"):
                    if not det_df.empty:
                        for _, d_row in det_df.iterrows():
                            st.write(f"・{d_row['項目名']}: {int(d_row['予算額']):,}円")

    st.write("---")
    surplus_pool = available_total - fc_total - ev_total_all
    st.subheader(f"予算ベースの余剰プール: {surplus_pool:,}円")

# ==========================================
# タブ5：イベント状態制御 
# ==========================================
with tab5:
    st.header("🎪 イベント状態制御 (予実管理)")
    st.caption("イベント完了時にチェックを入れると、計画予算と実際の支払額の差分（浮いたお金）が余剰プールへ還元されます。")
    
    if not df_ev.empty:
        df_ev_year = df_ev[df_ev['対象年'] == target_year_ui]
        if not df_ev_year.empty:
            df_ev_year['実績額'] = df_ev_year['実績額'].astype(int)
            edited_ev = st.data_editor(
                df_ev_year,
                column_config={
                    "is_included": st.column_config.CheckboxColumn("シミュレーション(ON)"),
                    "is_completed": st.column_config.CheckboxColumn("イベント完了(差分還元)"),
                },
                disabled=["イベントID", "イベント名", "対象年", "総予算", "実績額", "作成日"],
                use_container_width=True, hide_index=True
            )
            if st.button("🔄 完了状態を保存して差分をマージ"):
                for idx, row in edited_ev.iterrows():
                    df_ev.loc[df_ev['イベントID'] == row['イベントID'], ['is_included', 'is_completed']] = [row['is_included'], row['is_completed']]
                update_events(df_ev)
                st.cache_data.clear()
                st.success("状態を更新し、余剰プールへの差分マージ処理を発火しました。")
                st.rerun()
        else:
            st.info(f"{target_year_ui}年に登録されているイベントはありません。")
    else:
        st.info("現在登録されているイベントはありません。")