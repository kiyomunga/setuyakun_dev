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

# --- 2. データベースコネクション ---
conn = st.connection("gsheets", type=GSheetsConnection)

def fetch_data(worksheet, cols):
    df = conn.read(worksheet=worksheet, usecols=list(range(cols)))
    return df.dropna(how="all")

def get_sources(): return fetch_data("sources", 2)
def update_sources(df): conn.update(worksheet="sources", data=df)

def get_transactions(): return fetch_data("transactions", 7)
def update_transactions(df): conn.update(worksheet="transactions", data=df)

def get_events():
    df = fetch_data("events", 7)
    if not df.empty:
        # 文字列のTRUE/FALSEをPythonのBooleanに変換（安全策）
        df['is_included'] = df['is_included'].replace({'TRUE': True, 'FALSE': False, 'True': True, 'False': False}).fillna(False).astype(bool)
        df['is_completed'] = df['is_completed'].replace({'TRUE': True, 'FALSE': False, 'True': True, 'False': False}).fillna(False).astype(bool)
    return df
def update_events(df): conn.update(worksheet="events", data=df)

def get_config(): return fetch_data("config", 4)
def update_config(df): conn.update(worksheet="config", data=df)

# --- 3. 演算エンジン（プール計算と状態管理） ---
def calculate_system_state():
    state = {}
    
    # 財源の現在残高計算
    df_src = get_sources()
    df_tx = get_transactions()
    
    # NaNや空白の処理
    df_tx['金額'] = pd.to_numeric(df_tx['金額'], errors='coerce').fillna(0)
    df_tx['イベントID'] = df_tx['イベントID'].fillna('')
    
    balances = {str(row['財源名']): int(row['初期残高']) for _, row in df_src.iterrows()}
    
    # 資産総額のフェッチ
    if not df_tx.empty:
        for _, row in df_tx.iterrows():
            src, dest, amt = str(row['出金元']), str(row['入金先']), int(row['金額'])
            if src in balances: balances[src] -= amt
            if dest in balances: balances[dest] += amt
    state['balances'] = balances
    state['total_assets'] = sum(balances.values()) if balances else 0
    
    # コンフィグ（予算設定）のフェッチ
    df_conf = get_config()
    if not df_conf.empty:
        c = df_conf.iloc[0]
        base_saving = int(c['前年末貯蓄'])
        est_income = int(c['当年予測収入'])
        target_saving = int(c['当年末目標貯蓄'])
    else:
        base_saving = est_income = target_saving = 0
    
    base_pool = (base_saving + est_income) - target_saving
    
    # イベント実績の動的集計
    df_exp = df_tx[df_tx['入金先'] == '外部'] # 支出トランザクション
    df_inc = df_tx[df_tx['出金元'] == '外部'] # 収入トランザクション
    
    event_actuals = df_exp.groupby('イベントID')['金額'].sum().to_dict()
    
    df_ev = get_events()
    reserved_budget = 0
    
    if not df_ev.empty:
        # 最新の実績額をイベントデータフレームにマージ
        df_ev['実績額'] = df_ev['イベントID'].map(event_actuals).fillna(0)
        
        # 進行中（is_completed=False）かつ シミュレーションON（is_included=True）のイベントの予算をホールド
        active_events = df_ev[(df_ev['is_included'] == True) & (df_ev['is_completed'] == False)]
        # 予約リソース ＝ 予算 － 既に支払った実績額（0未満にはしない）
        active_events['予約残額'] = (active_events['総予算'] - active_events['実績額']).clip(lower=0)
        reserved_budget = active_events['予約残額'].sum()
    
    # 例外インフロー（臨時収入）の加算
    extra_income = df_inc[df_inc['カテゴリ'] == '臨時収入']['金額'].sum()
    
    # 実績支出の総計
    total_actual_exp = df_exp['金額'].sum()
    
    # 動的キャッシュプール（余剰プール）の算出
    current_surplus_pool = base_pool + extra_income - total_actual_exp - reserved_budget
    state['surplus_pool'] = current_surplus_pool
    
    # 月次ペースメーカー
    now = datetime.datetime.now()
    remaining_months = 12 - now.month + 1
    monthly_limit = current_surplus_pool / remaining_months if remaining_months > 0 else current_surplus_pool
    state['monthly_limit'] = monthly_limit
    
    # 当月の非イベント（ベースロード）支出
    current_month_str = now.strftime('%Y-%m')
    df_this_month = df_exp[(df_exp['日付'].str.startswith(current_month_str)) & (df_exp['イベントID'] == '')]
    this_month_spent = df_this_month['金額'].sum()
    state['this_month_spent'] = this_month_spent
    state['this_month_remaining'] = monthly_limit - this_month_spent
    
    return state, df_tx, df_ev, df_conf

# --- 4. フロントエンド・レンダリング ---
st.title('資産管理システム Ver.4.0 (予測型エンジン)')

state, df_tx, df_ev, df_conf = calculate_system_state()

# タブ構成の再定義
tab1, tab2, tab3, tab4 = st.tabs(['📊 ダッシュボード', '📝 トランザクション記録', '📅 イベント駆動制御', '⚙️ KGI設定'])

# ==========================================
# タブ1：ダッシュボード（月次ペースメーカー）
# ==========================================
with tab1:
    st.header("🎯 KGIマイルストーン & 予算監視")
    
    # KPIメトリクスのレンダリング
    col1, col2, col3 = st.columns(3)
    col1.metric("現在の総資産額（ハードウェア）", f"¥{state['total_assets']:,}")
    col2.metric("余剰プール（理論上の利用可能額）", f"¥{int(state['surplus_pool']):,}", "ベースロード + 予約予算控除後")
    
    # バーンレート監視（赤字ワーニング制御）
    remaining = int(state['this_month_remaining'])
    delta_color = "normal" if remaining >= 0 else "inverse"
    col3.metric(f"{datetime.datetime.now().month}月の利用残枠（ペースメーカー）", f"¥{remaining:,}", f"今月消費: ¥{int(state['this_month_spent']):,}", delta_color=delta_color)

    st.write("---")
    st.subheader('月別・目的別 支出分析（ROI可視化）')
    
    if not df_tx.empty:
        df_exp = df_tx[df_tx['入金先'] == '外部'].copy()
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
            st.info('支出データがありません。')

# ==========================================
# タブ2：トランザクション記録（動的減算処理）
# ==========================================
with tab2:
    st.subheader('お金の出入りを記録')
    source_options = ['外部'] + list(state['balances'].keys()) if state['balances'] else ['外部']
    
    # 汎用性とイベント分離のための9カテゴリ + 臨時収入
    category_options = ['食費', '学業', '移動費', 'スポーツ', '趣味・娯楽', '日用品', '交際費', '資金移動', '臨時収入', 'その他']
    event_options = [''] + df_ev['イベントID'].tolist() if not df_ev.empty else ['']
    
    with st.form(key='tx_form', clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            tx_date = st.date_input('日付', datetime.date.today())
            tx_src = st.selectbox('出金先', source_options, index=0)
            tx_dest = st.selectbox('入金先', source_options, index=0 if len(source_options) == 1 else 1)
            tx_amt = st.number_input('金額（円）', min_value=1, step=100)
        with col2:
            tx_cat = st.selectbox('カテゴリ', category_options)
            tx_event = st.selectbox('紐付けイベント（該当する場合）', event_options)
            tx_memo = st.text_input('メモ')
            
        if st.form_submit_button('トランザクションを実行'):
            if tx_src == '外部' and tx_dest == '外部':
                st.error('出金先と入金先の両方が外部の取引は無効です。')
            elif tx_src == tx_dest:
                st.error('同じ財源が選択されています。')
            else:
                new_row = [tx_date.strftime('%Y-%m-%d'), tx_src, tx_dest, int(tx_amt), tx_memo, tx_cat, tx_event]
                if df_tx.empty:
                    df_tx = pd.DataFrame(columns=['日付', '出金元', '入金先', '金額', 'メモ', 'カテゴリ', 'イベントID'])
                df_tx.loc[len(df_tx)] = new_row
                update_transactions(df_tx)
                st.cache_data.clear()
                st.success('トランザクションをコミットしました。ベースロード計算へ即時反映されます。')
                st.rerun()

    st.write('---')
    st.subheader('📜 取引履歴一覧（直近50件）')
    if not df_tx.empty:
        st.dataframe(df_tx.iloc[::-1].head(50), use_container_width=True)

# ==========================================
# タブ3：イベント駆動制御 (What-If分析)
# ==========================================
with tab3:
    st.subheader("🎪 予算予約とステートマシン制御")
    st.caption("イベントを作成し、チェックボックスでシミュレーション（ON/OFF）と完了状態を制御します。")
    
    # 既存イベントのステータス制御エディタ
    if not df_ev.empty:
        edited_ev = st.data_editor(
            df_ev,
            column_config={
                "is_included": st.column_config.CheckboxColumn("シミュレーション(含める)"),
                "is_completed": st.column_config.CheckboxColumn("イベント完了(マージ)"),
            },
            disabled=["イベントID", "イベント名", "総予算", "実績額", "作成日"],
            use_container_width=True,
            hide_index=True
        )
        if st.button("🔄 ステート（状態）を保存して再計算"):
            update_events(edited_ev)
            st.cache_data.clear()
            st.success("状態を更新し、余剰プールへの差分マージ処理を発火しました。")
            st.rerun()
    else:
        st.info("現在登録されているイベントはありません。")

    st.write("---")
    with st.form(key='event_form', clear_on_submit=True):
        st.subheader("➕ 新規イベント定義")
        col1, col2 = st.columns(2)
        with col1:
            ev_id = st.text_input("イベントID（例: E001）")
            ev_name = st.text_input("イベント名（例: 夏合宿）")
        with col2:
            ev_budget = st.number_input("確保する総予算（円）", min_value=0, step=1000)
            
        if st.form_submit_button("イベントを生成"):
            if ev_id == "" or ev_name == "":
                st.error("IDと名前は必須です。")
            elif not df_ev.empty and ev_id in df_ev['イベントID'].values:
                st.error("そのイベントIDは既に存在します。")
            else:
                if df_ev.empty:
                    df_ev = pd.DataFrame(columns=['イベントID', 'イベント名', '総予算', '実績額', 'is_included', 'is_completed', '作成日'])
                df_ev.loc[len(df_ev)] = [ev_id, ev_name, ev_budget, 0, True, False, datetime.date.today().strftime('%Y-%m-%d')]
                update_events(df_ev)
                st.cache_data.clear()
                st.rerun()

# ==========================================
# タブ4：KGI設定 (全体予算モジュール)
# ==========================================
with tab4:
    st.subheader("⚙️ 全体キャッシュプール設定")
    st.caption("東大院合格・アクチュアリー・競技目標から逆算した基本パラメータを入力します。")
    
    with st.form(key='config_form'):
        if not df_conf.empty:
            c = df_conf.iloc[0]
            val_base = int(c['前年末貯蓄'])
            val_est = int(c['当年予測収入'])
            val_target = int(c['当年末目標貯蓄'])
        else:
            val_base = val_est = val_target = 0
            
        c1, c2, c3 = st.columns(3)
        with c1: in_base = st.number_input("前年末貯蓄（円）", value=val_base, step=10000)
        with c2: in_est = st.number_input("当年予測収入（円）", value=val_est, step=10000)
        with c3: in_target = st.number_input("当年末目標貯蓄（円）", value=val_target, step=10000)
        
        if st.form_submit_button("設定をコンパイル"):
            new_conf = pd.DataFrame([['', '', '', '']], columns=['前年末貯蓄', '当年予測収入', '当年末目標貯蓄', '基準年'])
            new_conf.loc[0] = [in_base, in_est, in_target, datetime.datetime.now().year]
            update_config(new_conf)
            st.cache_data.clear()
            st.success("パラメータを更新し、全体のランウェイを再計算しました。")
            st.rerun()

    st.write("---")
    st.subheader("🏦 財源管理")
    with st.form(key='add_source_form', clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1: new_source = st.text_input('➕ 新しい財源の名前')
        with col2: init_balance = st.number_input('初期残高（円）', min_value=0, step=1000)
        if st.form_submit_button('財源を追加'):
            if new_source.strip() == '' or new_source == '外部':
                st.error('無効な名前です。')
            else:
                df_src = get_sources()
                df_src.loc[len(df_src)] = [new_source, init_balance]
                update_sources(df_src)
                st.cache_data.clear()
                st.rerun()