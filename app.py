import streamlit as st
import pandas as pd
import datetime
from streamlit_gsheets import GSheetsConnection

# 1. データベース接続の確立（シークレットから認証情報を自動取得）
conn = st.connection("gsheets", type=GSheetsConnection)

# データベースI/O用のラッパー関数群（ローカルキャッシュをバイパスして常に最新を取得）
def get_sources():
    df = conn.read(worksheet="sources", usecols=[0, 1])
    return df.dropna(how="all")

def update_sources(df):
    conn.update(worksheet="sources", data=df)

def get_transactions():
    df = conn.read(worksheet="transactions", usecols=[0, 1, 2, 3, 4])
    return df.dropna(how="all")

def update_transactions(df):
    conn.update(worksheet="transactions", data=df)

# 2. 現在の残高をリアルタイム計算する関数
def get_current_balances():
    df_src = get_sources()
    df_tx = get_transactions()
    
    balances = {str(row['財源名']): int(row['初期残高']) for _, row in df_src.iterrows()}
    
    if not df_tx.empty:
        for _, row in df_tx.iterrows():
            src = str(row['出金元'])
            dest = str(row['入金先'])
            # 金額が文字列として取得された場合の例外処理
            try:
                amt = int(row['金額'])
            except ValueError:
                amt = 0
            
            if src in balances:
                balances[src] -= amt
            if dest in balances:
                balances[dest] += amt
            
    return balances

# 3. メイン画面の構築
st.title('せつやくん')

tab1, tab2 = st.tabs(['📊 メインメニュー（資産管理）', '📝 記録メニュー'])

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
        st.info('登録されている財源がありません。下のフォームから追加してください。')

    st.write('---')
    st.subheader('⚙️ 財源の編集（追加・削除）')
    col_add, col_del = st.columns(2)
    
    with col_add:
        st.markdown('**➕ 新しい財源を追加**')
        with st.form(key='add_source_form', clear_on_submit=True):
            new_source = st.text_input('財源の名前（例: 貯金箱、LINE Pay）')
            init_balance = st.number_input('追加時の初期残高（円）', min_value=0, step=1000)
            submit_add = st.form_submit_button('追加を実行')
            
            if submit_add:
                if new_source.strip() == '':
                    st.error('名前が空欄です。')
                elif new_source in balances or new_source == '外部':
                    st.error('その名前は既に使われているか、使用できません。')
                else:
                    df_src = get_sources()
                    df_src.loc[len(df_src)] = [new_source, init_balance]
                    update_sources(df_src)
                    # キャッシュのクリアと再読み込み
                    st.cache_data.clear()
                    st.success(f'「{new_source}」を新しい財源として登録しました！')
                    st.rerun()

    with col_del:
        st.markdown('**❌ 既存の財源を削除**')
        with st.form(key='del_source_form'):
            del_target = st.selectbox('削除する財源を選択', list(balances.keys()) if balances else [])
            submit_del = st.form_submit_button('削除を実行')
            
            if submit_del and del_target:
                df_tx = get_transactions()
                has_history = False
                if not df_tx.empty:
                    has_history = ((df_tx['出金元'] == del_target) | (df_tx['入金先'] == del_target)).any()
                
                if has_history:
                    st.error(f'【保護】「{del_target}」は過去の取引履歴で使用されているため削除できません。')
                else:
                    df_src = get_sources()
                    df_src = df_src[df_src['財源名'] != del_target]
                    update_sources(df_src)
                    st.cache_data.clear()
                    st.success(f'「{del_target}」を削除しました。')
                    st.rerun()

# --- 【タブ2】記録メニュー ---
with tab2:
    st.subheader('お金の出入りを記録')
    
    balances = get_current_balances()
    source_options = ['外部'] + list(balances.keys()) if balances else ['外部']
    
    with st.form(key='transaction_form', clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            tx_date = st.date_input('日付', datetime.date.today())
            tx_src = st.selectbox('出金先（お金が減る場所）', source_options, index=0)
            tx_amt = st.number_input('金額（円）', min_value=1, step=100)
        with col2:
            tx_dest = st.selectbox('入金先（お金が増える場所）', source_options, index=0 if len(source_options) == 1 else 1)
            tx_memo = st.text_input('メモ（品目や理由など）')
            
        submit_tx = st.form_submit_button('この内容で記録する')
        
        if submit_tx:
            if tx_src == '外部' and tx_dest == '外部':
                st.error('出金先と入金先の両方を「外部」にすることはできません。')
            elif tx_src == tx_dest:
                st.error('同じ財源が選択されています。資金の移動が成立しません。')
            else:
                df_tx = get_transactions()
                # DataFrameが空の場合の列定義の保証
                if df_tx.empty:
                    df_tx = pd.DataFrame(columns=['日付', '出金元', '入金先', '金額', 'メモ'])
                    
                df_tx.loc[len(df_tx)] = [tx_date.strftime('%Y-%m-%d'), tx_src, tx_dest, int(tx_amt), tx_memo]
                update_transactions(df_tx)
                st.cache_data.clear()
                st.success('記録しました！')
                st.rerun()

    st.write('---')
    st.subheader('📋 財源すべての金の出入り（タイムライン）')
    
    df_tx = get_transactions()
    
    if not df_tx.empty:
        display_rows = []
        for _, row in df_tx.iterrows():
            if str(row['出金元']) != '外部':
                display_rows.append({
                    '日付': str(row['日付']),
                    '財源': str(row['出金元']),
                    '変動額': -int(row['金額']),
                    'メモ': f"➔ 【{row['入金先']}】へ移動・支出 | {row['メモ']}" if pd.notnull(row['メモ']) and str(row['メモ']) != '' and str(row['メモ']) != 'nan' else f"➔ 【{row['入金先']}】へ移動・支出"
                })
            if str(row['入金先']) != '外部':
                display_rows.append({
                    '日付': str(row['日付']),
                    '財源': str(row['入金先']),
                    '変動額': int(row['金額']),
                    'メモ': f"⬅ 【{row['出金元']}】から移動・収入 | {row['メモ']}" if pd.notnull(row['メモ']) and str(row['メモ']) != '' and str(row['メモ']) != 'nan' else f"⬅ 【{row['出金元']}】から移動・収入"
                })
                
        df_display = pd.DataFrame(display_rows)
        
        if not df_display.empty:
            df_display = df_display.sort_values(by='日付', ascending=False)
            
            html = "<table style='width:100%; border-collapse: collapse; font-family: sans-serif; font-size: 14px;'>"
            html += "<thead><tr style='border-bottom: 2px solid #64748b; background-color: rgba(100,116,139,0.05);'>"
            html += "<th style='text-align:left; padding:10px;'>日付</th><th style='text-align:left; padding:10px;'>財源</th><th style='text-align:right; padding:10px;'>変動額</th><th style='text-align:left; padding:10px;'>内訳・メモ</th>"
            html += "</tr></thead><tbody>"
            
            for _, row in df_display.iterrows():
                amt = row['変動額']
                if amt > 0:
                    amt_str = f"<span style='color:#3b82f6; font-weight:bold;'>+{amt:,}</span>"
                    row_bg = "rgba(59,130,246,0.02)"
                else:
                    amt_str = f"<span style='color:#ef4444; font-weight:bold;'>{amt:,}</span>"
                    row_bg = "rgba(239,68,68,0.02)"
                    
                html += f"<tr style='border-bottom: 1px solid #e2e8f0; background-color: {row_bg};'>"
                html += f"<td style='padding:10px; color:#64748b;'>{row['日付']}</td>"
                html += f"<td style='padding:10px; font-weight:500;'>{row['財源']}</td>"
                html += f"<td style='text-align:right; padding:10px; font-size:15px;'>{amt_str}</td>"
                html += f"<td style='padding:10px; color:#334155;'>{row['メモ']}</td>"
                html += "</tr>"
                
            html += "</tbody></table>"
            st.markdown(html, unsafe_allow_html=True)
    else:
        st.caption('履歴データがありません。最初の取引を記録してください。')