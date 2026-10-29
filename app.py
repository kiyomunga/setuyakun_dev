import streamlit as st
import pandas as pd
import datetime
from streamlit_gsheets import GSheetsConnection
import plotly.express as px

# --- 1. UI・システム設定 ---
st.set_page_config(
    page_title="せつやくん - 予測型資産管理",
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

# --- 3. 演算エンジン（プール計算と状態管理） ---
def calculate_system_state(target_year):
    state = {}
    
    df_src = get_sources()
    df_tx = get_transactions()
    df_tx['金額'] = pd.to_numeric(df_tx['金額'], errors='coerce').fillna(0)
    df_tx['イベントID'] = df_tx['イベントID'].fillna('')
    df_tx['年'] = pd.to_datetime(df_tx['日付'], errors='coerce').dt.year
    
    balances = {str(row['財源名']): int(row['初期残高']) for _, row in df_src.iterrows()}
    
    if not df_tx.empty:
        for _, row in df_tx.iterrows():
            src, dest, amt = str(row['出金元']), str(row['入金先']), int(row['金額'])
            if src in balances: balances[src] -= amt
            if dest in balances: balances[dest] += amt
    state['total_assets'] = sum(balances.values()) if balances else 0
    state['balances'] = balances
    
    df_conf = get_config()
    df_conf['対象年'] = pd.to_numeric(df_conf['対象年'], errors='coerce')
    conf_target = df_conf[df_conf['対象年'] == target_year]
    
    if not conf_target.empty:
        c = conf_target.iloc[0]
        base_saving = int(c['前年末貯蓄'])
        est_income = int(c['予測収入'])
        target_saving = int(c['目標貯蓄'])
    else:
        base_saving = est_income = target_saving = 0
    
    base_pool = (base_saving + est_income) - target_saving
    
    df_exp_year = df_tx[(df_tx['入金先'] == '外部') & (df_tx['年'] == target_year)]
    df_inc_year = df_tx[(df_tx['出金元'] == '外部') & (df_tx['年'] == target_year)]
    
    event_actuals = df_exp_year.groupby('イベントID')['金額'].sum().to_dict()
    
    df_ev = get_events()
    df_ev_details = get_event_details()
    reserved_budget = 0
    
    if not df_ev.empty:
        # イベント内訳から総予算を動的計算（リレーションの統合）
        if not df_ev_details.empty:
            df_ev_details['予算額'] = pd.to_numeric(df_ev_details['予算額'], errors='coerce').fillna(0)
            budget_map = df_ev_details.groupby('イベントID')['予算額'].sum().to_dict()
            df_ev['総予算'] = df_ev['イベントID'].map(budget_map).fillna(0)
        else:
            df_ev['総予算'] = pd.to_numeric(df_ev['総予算'], errors='coerce').fillna(0)

        df_ev['実績額'] = df_ev['イベントID'].map(event_actuals).fillna(0)
        
        # 指定された年のイベントのみを対象に予約リソースを計算
        active_events = df_ev[(df_ev['対象年'] == target_year) & (df_ev['is_included'] == True) & (df_ev['is_completed'] == False)]
        active_events['予約残額'] = (active_events['総予算'] - active_events['実績額']).clip(lower=0)
        reserved_budget = active_events['予約残額'].sum()
    
    extra_income = df_inc_year[df_inc_year['カテゴリ'] == '臨時収入']['金額'].sum()
    total_actual_exp = df_exp_year['金額'].sum()
    
    current_surplus_pool = base_pool + extra_income - total_actual_exp - reserved_budget
    state['surplus_pool'] = current_surplus_pool
    
    now = datetime.datetime.now()
    if target_year == now.year:
        remaining_months = 12 - now.month + 1
        current_month_str = now.strftime('%Y-%m')
        df_this_month = df_exp_year[(df_exp_year['日付'].str.startswith(current_month_str)) & (df_exp_year['イベントID'] == '')]
        this_month_spent = df_this_month['金額'].sum()
    else:
        remaining_months = 12
        this_month_spent = 0
        
    monthly_limit = current_surplus_pool / remaining_months if remaining_months > 0 else 0
    state['monthly_limit'] = monthly_limit
    state['this_month_spent'] = this_month_spent
    state['this_month_remaining'] = monthly_limit - this_month_spent
    
    return state, df_tx, df_ev, df_conf, df_ev_details

# --- 4. フロントエンド・レンダリング ---
st.title('資産管理システム Ver.4.1 (予測型エンジン)')

current_year = datetime.datetime.now().year
target_year_ui = st.sidebar.number_input("📅 基準年（ダッシュボード・シミュレーション対象）", min_value=2020, max_value=2050, value=current_year, step=1)

state, df_tx, df_ev, df_conf, df_ev_details = calculate_system_state(target_year_ui)

tab1, tab2, tab3, tab4 = st.tabs(['📊 ダッシュボード', '📝 トランザクション記録', '🎪 イベント内訳・予算制御', '⚙️ KGI設定'])

# ==========================================
# タブ1：ダッシュボード
# ==========================================
with tab1:
    st.header(f"🎯 KGIマイルストーン & 予算監視 ({target_year_ui}年)")
    
    col1, col2, col3 = st.columns(3)
    col1.metric("現在の総資産額（ハードウェア）", f"¥{state['total_assets']:,}")
    col2.metric(f"余剰プール（{target_year_ui}年分）", f"¥{int(state['surplus_pool']):,}", "ベースロード + 予約予算控除後")
    
    if target_year_ui == current_year:
        remaining = int(state['this_month_remaining'])
        delta_color = "normal" if remaining >= 0 else "inverse"
        col3.metric(f"{datetime.datetime.now().month}月の利用残枠（ペースメーカー）", f"¥{remaining:,}", f"今月消費: ¥{int(state['this_month_spent']):,}", delta_color=delta_color)
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
                fig = px.pie(df_grouped, values='金額', names='カテゴリ', hole=0.4, 
                             title=f'{selected_month} リソース配分比率',
                             color_discrete_sequence=px.colors.qualitative.Pastel)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info('選択した月のデータがありません。')
        else:
            st.info(f'{target_year_ui}年の支出データがありません。')

# ==========================================
# タブ2：トランザクション記録
# ==========================================
with tab2:
    st.subheader('お金の出入りを記録')
    source_options = ['外部'] + list(state['balances'].keys()) if state['balances'] else ['外部']
    category_options = ['食費', '学業', '移動費', 'スポーツ', '趣味・娯楽', '日用品', '交際費', '資金移動', '臨時収入', 'その他']
    event_options = [''] + df_ev[df_ev['対象年'] == target_year_ui]['イベントID'].tolist() if not df_ev.empty else ['']
    
    with st.form(key='tx_form', clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            tx_date = st.date_input('日付', datetime.date.today())
            tx_src = st.selectbox('出金先', source_options, index=0)
            tx_dest = st.selectbox('入金先', source_options, index=0 if len(source_options) == 1 else 1)
            tx_amt = st.number_input('金額（円）', min_value=1, step=100)
        with col2:
            tx_cat = st.selectbox('カテゴリ', category_options)
            tx_event = st.selectbox(f'紐付けイベント ({target_year_ui}年)', event_options)
            tx_memo = st.text_input('メモ')
            
        if st.form_submit_button('トランザクションを実行'):
            if tx_src == '外部' and tx_dest == '外部': st.error('無効な取引です。')
            elif tx_src == tx_dest: st.error('同じ財源が選択されています。')
            else:
                new_row = [tx_date.strftime('%Y-%m-%d'), tx_src, tx_dest, int(tx_amt), tx_memo, tx_cat, tx_event]
                if df_tx.empty: df_tx = pd.DataFrame(columns=['日付', '出金元', '入金先', '金額', 'メモ', 'カテゴリ', 'イベントID'])
                df_tx.loc[len(df_tx)] = new_row
                update_transactions(df_tx)
                st.cache_data.clear()
                st.rerun()

# ==========================================
# タブ3：イベント駆動制御 (What-If分析 & 内訳)
# ==========================================
with tab3:
    st.subheader(f"🎪 予算予約とステートマシン制御 ({target_year_ui}年)")
    
    if not df_ev.empty:
        df_ev_year = df_ev[df_ev['対象年'] == target_year_ui]
        if not df_ev_year.empty:
            edited_ev = st.data_editor(
                df_ev_year,
                column_config={
                    "is_included": st.column_config.CheckboxColumn("シミュレーション(含める)"),
                    "is_completed": st.column_config.CheckboxColumn("イベント完了(マージ)"),
                },
                disabled=["イベントID", "イベント名", "対象年", "総予算", "実績額", "作成日"],
                use_container_width=True, hide_index=True
            )
            if st.button("🔄 ステート（状態）を保存して再計算"):
                for idx, row in edited_ev.iterrows():
                    df_ev.loc[df_ev['イベントID'] == row['イベントID'], ['is_included', 'is_completed']] = [row['is_included'], row['is_completed']]
                update_events(df_ev)
                st.cache_data.clear()
                st.rerun()
                
            st.write("---")
            st.subheader("📋 イベント詳細内訳（費目の設定）")
            selected_ev_id = st.selectbox("詳細を編集するイベントを選択", df_ev_year['イベントID'].tolist())
            
            if selected_ev_id:
                df_details_ev = df_ev_details[df_ev_details['イベントID'] == selected_ev_id] if not df_ev_details.empty else pd.DataFrame(columns=['イベントID', '項目名', '予算額'])
                
                with st.form(key='add_detail_form', clear_on_submit=True):
                    dc1, dc2 = st.columns(2)
                    with dc1: detail_name = st.text_input("項目名（例：宿泊費、交通費）")
                    with dc2: detail_amt = st.number_input("予算額（円）", min_value=0, step=500)
                    if st.form_submit_button("内訳を追加"):
                        if df_ev_details.empty: df_ev_details = pd.DataFrame(columns=['イベントID', '項目名', '予算額'])
                        df_ev_details.loc[len(df_ev_details)] = [selected_ev_id, detail_name, detail_amt]
                        update_event_details(df_ev_details)
                        st.cache_data.clear()
                        st.rerun()
                
                if not df_details_ev.empty:
                    st.dataframe(df_details_ev[['項目名', '予算額']], use_container_width=True)
                    ev_total = df_details_ev['予算額'].astype(int).sum()
                    st.info(f"現在の内訳合計額: ¥{ev_total:,} （※これがイベント総予算として自動計算されます）")
        else:
            st.info(f"{target_year_ui}年に登録されているイベントはありません。")

    st.write("---")
    with st.form(key='event_form', clear_on_submit=True):
        st.subheader("➕ 新規イベント定義")
        col1, col2 = st.columns(2)
        with col1:
            ev_id = st.text_input("イベントID（例: E2026-01）")
            ev_name = st.text_input("イベント名（例: 夏合宿）")
        with col2:
            ev_year = st.number_input("対象年", value=target_year_ui, step=1)
            
        if st.form_submit_button("イベントを生成"):
            if ev_id == "" or ev_name == "": st.error("IDと名前は必須です。")
            elif not df_ev.empty and ev_id in df_ev['イベントID'].values: st.error("そのイベントIDは既に存在します。")
            else:
                if df_ev.empty: df_ev = pd.DataFrame(columns=['イベントID', 'イベント名', '総予算', '実績額', 'is_included', 'is_completed', '作成日', '対象年'])
                df_ev.loc[len(df_ev)] = [ev_id, ev_name, 0, 0, True, False, datetime.date.today().strftime('%Y-%m-%d'), ev_year]
                update_events(df_ev)
                st.cache_data.clear()
                st.rerun()

# ==========================================
# タブ4：KGI設定 (全体予算モジュール)
# ==========================================
with tab4:
    st.subheader(f"⚙️ 全体キャッシュプール設定 ({target_year_ui}年)")
    
    df_conf_year = df_conf[df_conf['対象年'] == target_year_ui] if not df_conf.empty else pd.DataFrame()
    with st.form(key='config_form'):
        if not df_conf_year.empty:
            c = df_conf_year.iloc[0]
            val_base = int(c['前年末貯蓄'])
            val_est = int(c['予測収入'])
            val_target = int(c['目標貯蓄'])
        else:
            val_base = val_est = val_target = 0
            
        c1, c2, c3 = st.columns(3)
        with c1: in_base = st.number_input("前年末貯蓄（円）", value=val_base, step=10000)
        with c2: in_est = st.number_input("予測収入（円）", value=val_est, step=10000)
        with c3: in_target = st.number_input("目標貯蓄（円）", value=val_target, step=10000)
        
        if st.form_submit_button(f"{target_year_ui}年の設定をコンパイル"):
            if df_conf.empty: df_conf = pd.DataFrame(columns=['対象年', '前年末貯蓄', '予測収入', '目標貯蓄'])
            df_conf = df_conf[df_conf['対象年'] != target_year_ui] # 既存の同一年データを削除
            df_conf.loc[len(df_conf)] = [target_year_ui, in_base, in_est, in_target]
            update_config(df_conf)
            st.cache_data.clear()
            st.rerun()