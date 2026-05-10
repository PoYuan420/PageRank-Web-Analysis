import streamlit as st
import requests
from bs4 import BeautifulSoup
import networkx as nx
import pandas as pd
import plotly.express as px
from urllib.parse import urljoin, urlparse
import time
from pyvis.network import Network
import streamlit.components.v1 as components
import os

# --- 1. 爬蟲核心邏輯 ---
def get_links(url, headers, max_links):
    links = set()
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                full_url = urljoin(url, a['href'])
                if urlparse(full_url).scheme in ['http', 'https']:
                    links.add(full_url)
                if len(links) >= max_links:
                    break
    except:
        pass
    return links

def crawl_web(start_url, max_per_layer=20):
    G = nx.DiGraph()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    st.write(f"🔍 正在啟動分析：{start_url}")
    layer1_links = get_links(start_url, headers, max_per_layer)
    for link in layer1_links:
        G.add_edge(start_url, link)
        
    progress_bar = st.progress(0)
    total = len(layer1_links)
    
    for i, link in enumerate(layer1_links):
        l2_links = get_links(link, headers, max_per_layer)
        for l2 in l2_links:
            G.add_edge(link, l2)
        progress_bar.progress((i + 1) / total)
        time.sleep(0.05)
    return G

# --- 2. 評分與視覺化組件 ---
def draw_influence_bar(current_val, df):
    # 計算分數：以最大權重為100分基準進行線性縮放
    max_val = df['權重值'].max()
    score = (current_val / max_val) * 100 if max_val > 0 else 0
    score = round(score, 1)

    # 判定區間顏色與標籤
    if score <= 20: label, color = "極弱", "#9ca3af"
    elif score <= 40: label, color = "弱", "#fbbf24"
    elif score <= 60: label, color = "一般", "#60a5fa"
    elif score <= 80: label, color = "強", "#8b5cf6"
    else: label, color = "極強", "#ef4444"

    # HTML 繪製橫向進度條
    bar_html = f"""
    <div style="font-family: sans-serif; margin: 20px 0;">
        <div style="display: flex; justify-content: space-between; margin-bottom: 5px;">
            <span style="font-weight: bold; color: {color};">影響力評級：{label}</span>
            <span style="font-weight: bold;">{score} / 100</span>
        </div>
        <div style="width: 100%; background-color: #e5e7eb; border-radius: 10px; height: 25px; position: relative;">
            <div style="width: {score}%; background-color: {color}; height: 100%; border-radius: 10px; transition: width 1s ease-in-out;"></div>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 10px; color: #6b7280; margin-top: 5px;">
            <span>0%</span><span>20%</span><span>40%</span><span>60%</span><span>80%</span><span>100%</span>
        </div>
    </div>
    """
    components.html(bar_html, height=100)

def draw_interactive_graph(G, df):
    # 建立 Pyvis 網路圖 (設定像親緣關係圖的力學效果)
    net = Network(height="600px", width="100%", bgcolor="#f8fafc", font_color="#1e293b", directed=True)
    
    # 取前 40 個最重要節點，避免畫面過度擁擠
    top_nodes = df['網址'].head(40).tolist()
    max_weight = df['權重值'].max()
    
    for _, row in df.head(40).iterrows():
        url = row['網址']
        # 節點大小根據 PageRank 權重動態調整
        node_size = 15 + (row['權重值'] / max_weight * 50)
        label = urlparse(url).netloc if len(url) > 20 else url
        net.add_node(url, label=label, title=url, size=node_size, color="#60a5fa")

    for u, v in G.edges():
        if u in top_nodes and v in top_nodes:
            net.add_edge(u, v, color="#cbd5e1", arrows="to")
    
    # 開啟力學模擬
    net.toggle_physics(True)
    net.set_options("""
    var options = {
      "physics": {
        "forceAtlas2Based": { "gravitationalConstant": -50, "centralGravity": 0.01, "springLength": 100 },
        "minVelocity": 0.75,
        "solver": "forceAtlas2Based"
      }
    }
    """)
    
    # 儲存並讀取為 Streamlit 組件
    try:
        net.save_graph("graph.html")
        with open("graph.html", 'r', encoding='utf-8') as f:
            components.html(f.read(), height=650)
    except:
        st.error("拓樸圖生成失敗，請確認是否具備檔案寫入權限。")

# --- 3. Streamlit 主介面 ---
st.set_page_config(page_title="PageRank 權重分析儀表板", layout="wide")
st.title("🕸️ 網頁影響力 PageRank 分析系統")

# 初始化記憶體
if 'G' not in st.session_state: st.session_state.G = None
if 'df' not in st.session_state: st.session_state.df = None

with st.sidebar:
    st.header("⚙️ 分析設定")
    start_url = st.text_input("起始網址", value="https://www.wikipedia.org")
    max_links = st.slider("每層爬取上限", 5, 50, 15)
    alpha = st.slider("阻尼係數 (Alpha)", 0.0, 1.0, 0.85)
    analyze_btn = st.button("開始執行深度分析")

if analyze_btn:
    with st.spinner("正在爬取網頁並建構關聯圖..."):
        G_res = crawl_web(start_url, max_links)
        # 執行 PageRank 運算
        pagerank_scores = nx.pagerank(G_res, alpha=alpha)
        
        df_res = pd.DataFrame(list(pagerank_scores.items()), columns=['網址', '權重值'])
        df_res = df_res.sort_values(by='權重值', ascending=False).reset_index(drop=True)
        
        st.session_state.G = G_res
        st.session_state.df = df_res

# 顯示結果區
if st.session_state.df is not None:
    df = st.session_state.df
    G = st.session_state.G

    st.divider()
    col1, col2 = st.columns([4, 6])

    with col1:
        st.subheader("🏆 權重值排名 (Top 10)")
        st.dataframe(df.head(10), use_container_width=True)
        
        st.subheader("📊 權重分佈佔比")
        fig_pie = px.pie(df.head(15), values='權重值', names='網址', hole=0.4)
        st.plotly_chart(fig_pie, use_container_width=True)

    with col2:
        st.subheader("🎯 關鍵指標與評分系統")
        selected_site = st.selectbox("請選擇欲分析的網頁：", df['網址'].tolist())
        
        # 繪製橫向評分條
        current_weight = df.loc[df['網址'] == selected_site, '權重值'].values[0]
        draw_influence_bar(current_weight, df)

        # 顯示下游細節
        successors = list(G.successors(selected_site))
        if successors:
            st.write(f"📍 該網頁連向了 **{len(successors)}** 個下游網頁。")
            sub_df = df[df['網址'].isin(successors)]
            fig_sub = px.bar(sub_df, x='權重值', y='網址', orientation='h', title="下游連結網頁權重對比")
            st.plotly_chart(fig_sub, use_container_width=True)
        else:
            st.warning("此網頁在本次分析層級中無下游連結。")

    st.divider()
    st.subheader("🌳 網頁關係拓樸圖 (互動式)")
    st.info("💡 提示：此圖模擬親緣關係分佈，愈大的節點代表 PageRank 影響力愈強。你可以用滑鼠拖曳網址節點。")
    draw_interactive_graph(G, df)

else:
    st.info("請於左側選單設定網址，並按下分析按鈕開始實作。")
