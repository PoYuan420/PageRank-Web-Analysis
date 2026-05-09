import streamlit as st
import requests
from bs4 import BeautifulSoup
import networkx as nx
import pandas as pd
import plotly.express as px
from urllib.parse import urljoin, urlparse
import time

# --- 1. 爬蟲核心邏輯 ---
def crawl_web(start_url, max_per_layer=20):
    """
    抓取兩層連結並建立網路圖
    """
    G = nx.DiGraph()
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # 第一層 (Layer 0 -> Layer 1)
    st.write(f"正在分析起始網頁: {start_url}")
    layer1_links = get_links(start_url, headers, max_per_layer)
    
    for link in layer1_links:
        G.add_edge(start_url, link)
        
    # 第二層 (Layer 1 -> Layer 2)
    progress_bar = st.progress(0)
    total = len(layer1_links)
    
    for i, link in enumerate(layer1_links):
        st.write(f"正在爬取網站 ({i+1}/{total}): {link[:50]}...")
        l2_links = get_links(link, headers, max_per_layer)
        for l2 in l2_links:
            G.add_edge(link, l2)
        progress_bar.progress((i + 1) / total)
        time.sleep(0.1) # 稍微休息避免被封鎖
        
    return G

def get_links(url, headers, max_links):
    links = set()
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                full_url = urljoin(url, a['href'])
                # 只過濾 http 格式
                if urlparse(full_url).scheme in ['http', 'https']:
                    links.add(full_url)
                if len(links) >= max_links:
                    break
    except Exception as e:
        pass # 忽略無法連線的網站
    return links

# --- 2. Streamlit 網頁介面 (條件二、五) ---
st.set_page_config(page_title="PageRank 影響力分析系統", layout="wide")
st.title("🌐 網頁權重分析")

with st.sidebar:
    st.header("設定參數")
    start_url = st.text_input("輸入起始網址", value="https://www.wikipedia.org")
    max_links = st.slider("每層最大抓取數", 5, 50, 20)
    alpha = st.slider("PageRank 阻尼係數 (Alpha)", 0.0, 1.0, 0.85)
    analyze_btn = st.button("開始執行分析")

# 初始化 session_state (這就像是程式的記憶體)
if 'G' not in st.session_state:
    st.session_state.G = None
if 'df' not in st.session_state:
    st.session_state.df = None

# 當按下按鈕時，執行運算並存入記憶體
if analyze_btn:
    with st.spinner("系統分析中，請稍候..."):
        # 執行爬蟲
        G_result = crawl_web(start_url, max_links)
        # 計算 PageRank
        pagerank_scores = nx.pagerank(G_result, alpha=alpha)
        # 整理數據
        df_result = pd.DataFrame(list(pagerank_scores.items()), columns=['網址', '權重值'])
        df_result = df_result.sort_values(by='權重值', ascending=False).reset_index(drop=True)
        
        # 存入記憶體
        st.session_state.G = G_result
        st.session_state.df = df_result

# --- 關鍵修改：只要記憶體裡有資料，就顯示分析結果 ---
if st.session_state.df is not None:
    df = st.session_state.df
    G = st.session_state.G
    
    st.divider()
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("🏆 最具影響力 Top 5")
        st.table(df.head(5))
        
        st.subheader("📊 完整權重排名")
        st.dataframe(df)

    with col2:
        st.subheader("💡 關鍵指標分析")
        avg_val = df['權重值'].mean()
        
        # 現在操作 selectbox 不會再消失了！
        selected_site = st.selectbox("選擇特定網頁查看占比細節", df['網址'].tolist())
        
        site_score = df.loc[df['網址'] == selected_site, '權重值'].values[0]
        if site_score > avg_val * 2:
            st.success(f"判定：此為【具高度影響力】節點。")
        else:
            st.info(f"判定：影響力一般。")

        successors = list(G.successors(selected_site))
        if successors:
            sub_df = df[df['網址'].isin(successors)]
            fig = px.pie(sub_df, values='權重值', names='網址', title='下游連結權重占比圖')
            st.plotly_chart(fig)
        else:
            st.warning("該網頁在本次分析中無下游連結。")

    # 底部關聯圖
    st.divider()
    st.subheader("🕸️ 網頁關聯拓樸圖 (局部)")
    small_G = G.subgraph(df['網址'].head(20))
    st.write(f"顯示前 20 名節點。節點總數: {G.number_of_nodes()}, 連線總數: {G.number_of_edges()}")

else:
    st.info("請在左側輸入網址並點擊『開始執行分析』。")