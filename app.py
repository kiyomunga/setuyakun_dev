import streamlit as st
import pandas as pd
import datetime
from streamlit_gsheets import GSheetsConnection
import plotly.express as px

# 1. ページ設定（UI最適化パッチ：必ず最初に記述）
st.set_page_config(
    page_title="せつやくん - 資産管理",
    page_icon="https://raw.githubusercontent.com/kiyomunga/setuyakun_dev/main/icon.png"
)

# 2. データベース接続
conn = st.connection("gsheets", type=GSheetsConnection)

def get_sources():
    df = conn.read(worksheet="sources", usecols=[0, 1])
    return df.dropna(how="all")

def update_sources(df):
    conn.update(worksheet="sources", data=df)

def get_transactions():
    # インデックス5（F列）の「カテゴリ」まで拡張ロード
    df = conn.read(worksheet="transactions", usecols=[0, 1, 2, 3, 4, 5])
    return df.dropna(how="all")

def update_transactions(df):
    conn.update(worksheet="transactions", data=df)

def get_current_balances():
    df_src = get_sources()
    df_tx = get_transactions()
    
    balances = {str(row['財源名']): int(row['初期残高']) for _, row in df_src.iterrows()}
    
    if not df_tx.empty:
        for _, row in df_tx.iterrows():
            src = str(row['出金元'])
            dest = str(row['入金先'])
            try:
                amt = int(row['金額'])
            except ValueError:
                amt = 0
            
            if src in balances:
                balances[src] -= amt
            if dest in balances:
                balances[dest] += amt
            
    return balances

st.title('資産管理システム Ver.3.0')

tab1, tab2, tab3 = st.tabs(['📊 メインメニュー', '📝 記録メニュー', '📈 分析ダッシュボード'])

# --- 【タブ1】メインメニュー ---
with tab1:
    st.subheader('現在の資産状況')
    balances = get_current_balances()
    if balances:
        total_assets = sum(balances.values())
        st.metric(label='総資産額（合算）', value=f"¥{total_assets:,}")
        st.write('---')
        cols = st.columns(len(balances))
        for idx, (source_name, balance) in enumerate(balances.items()):
            with cols[idx]:
                st.metric(label=source_name, value=f"¥{balance:,}")
    else:
        st.info('登録されている財源がありません。')

    st.write('---')
    st.subheader('⚙️ 財源の編集')
    col_add, col_del = st.columns(2)
    with col_add:
        with st.form(key='add_source_form', clear_on_submit=True):
            new_source = st.text_input('➕ 新しい財源の名前')
            init_balance = st.number_input('初期残高（円）', min_value=0, step=1000)
            if st.form_submit_button('追加を実行'):
                if new_source.strip() == '' or new_source in balances or new_source == '外部':
                    st.error('無効な名前、または既に使用されています。')
                else:
                    df_src = get_sources()
                    df_src.loc[len(df_src)] = [new_source, init_balance]
                    update_sources(df_src)
                    st.cache_data.clear()
                    st.rerun()

    with col_del:
        with st.form(key='del_source_form'):
            del_target = st.selectbox('❌ 削除する財源', list(balances.keys()) if balances else [])
            if st.form_submit_button('削除を実行') and del_target:
                df_tx = get_transactions()
                has_history = False
                if not df_tx.empty:
                    has_history = ((df_tx['出金元'] == del_target) | (df_tx['入金先'] == del_target)).any()
                if has_history:
                    st.error('保護：過去の取引履歴で使用されているため削除できません。')
                else:
                    df_src = get_sources()
                    df_src = df_src[df_src['財源名'] != del_target]
                    update_sources(df_src)
                    st.cache_data.clear()
                    st.rerun()

# --- 【タブ2】記録メニュー ---
with tab2:
    st.subheader('お金の出入りを記録')
    balances = get_current_balances()
    source_options = ['外部'] + list(balances.keys()) if balances else ['外部']
    
    # 戦略的KGIに基づくカテゴリ定義
    category_options = ['食費（機体燃料）', '教育・研究費（演算能力）', 'エンタメ・ライブ', '競技・遠征費', '日用品', '交際費', '資金移動', 'その他（浪費）']
    
    with st.form(key='transaction_form', clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            tx_date = st.date_input('日付', datetime.date.today())
            tx_src = st.selectbox('出金先', source_options, index=0)
            tx_amt = st.number_input('金額（円）', min_value=1, step=100)
        with col2:
            tx_dest = st.selectbox('入金先', source_options, index=0 if len(source_options) == 1 else 1)
            tx_cat = st.selectbox('カテゴリ（目的）', category_options)
            tx_memo = st.text_input('メモ')
            
        if st.form_submit_button('記録する'):
            if tx_src == '外部' and tx_dest == '外部':
                st.error('出金先と入金先の両方を「外部」にすることはできません。')
            elif tx_src == tx_dest:
                st.error('同じ財源が選択されています。')
            else:
                df_tx = get_transactions()
                if df_tx.empty:
                    df_tx = pd.DataFrame(columns=['日付', '出金元', '入金先', '金額', 'メモ', 'カテゴリ'])
                
                df_tx.loc[len(df_tx)] = [tx_date.strftime('%Y-%m-%d'), tx_src, tx_dest, int(tx_amt), tx_memo, tx_cat]
                update_transactions(df_tx)
                st.cache_data.clear()
                st.success('記録しました！')
                st.rerun()

# --- 【タブ3】分析ダッシュボード ---
with tab3:
    st.subheader('月別・目的別 支出分析（ROI可視化）')
    df_tx = get_transactions()
    
    if not df_tx.empty and 'カテゴリ' in df_tx.columns:
        df_exp = df_tx[df_tx['入金先'] == '外部'].copy()
        
        if not df_exp.empty:
            df_exp['年月'] = pd.to_datetime(df_exp['日付']).dt.strftime('%Y-%m')
            df_exp['金額'] = pd.to_numeric(df_exp['金額'], errors='coerce').fillna(0)
            
            months = sorted(df_exp['年月'].unique(), reverse=True)
            selected_month = st.selectbox('分析対象月', months)
            
            df_month = df_exp[df_exp['年月'] == selected_month]
            
            if not df_month.empty:
                df_grouped = df_month.groupby('カテゴリ')['金額'].sum().reset_index()
                
                fig = px.pie(df_grouped, values='金額', names='カテゴリ', hole=0.4, 
                             title=f'{selected_month} リソース配分比率',
                             color_discrete_sequence=px.colors.qualitative.Pastel)
                fig.update_traces(textposition='inside', textinfo='percent+label')
                st.plotly_chart(fig, use_container_width=True)
                
                st.dataframe(df_grouped.sort_values(by='金額', ascending=False), use_container_width=True)
            else:
                st.info('選択した月の支出データがありません。')
        else:
            st.info('外部への支出記録（消費）がまだありません。')
    else:
        st.warning('データを解析できません。以前の取引データに「カテゴリ」列が含まれていない可能性があります。')